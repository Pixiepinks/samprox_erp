from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import io
import re
import uuid
from typing import Any

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import (
    Company,
    ExsolInventoryItem,
    ExsolProductionEntry,
    ExsolProductionSerial,
    RoleEnum,
    User,
    normalize_role,
)


bp = Blueprint("exsol_production", __name__, url_prefix="/api/exsol/production")

EXSOL_COMPANY_KEY = "EXSOL"
EXSOL_COMPANY_NAME = "Exsol Engineering (Pvt) Ltd"
SHIFT_OPTIONS = {"Morning", "Evening", "Night"}
SERIAL_REGEX = re.compile(r"^[0-9]{8}$")
MAX_BULK_QUANTITY = 500
_EXSOL_SEQUENCES_READY = False


@dataclass
class ExsolProductionValidationError(Exception):
    errors: list[dict[str, Any]]

    def __str__(self) -> str:  # pragma: no cover - utility
        return "Exsol production validation error"


def _build_error(message: str, status: int = 400, details: list[str] | None = None):
    payload: dict[str, Any] = {"ok": False, "error": message}
    if details:
        payload["details"] = details
    return jsonify(payload), status


def _has_exsol_production_access() -> bool:
    try:
        claims = get_jwt()
    except Exception:
        return False

    role_raw = claims.get("role")
    company_key = (claims.get("company_key") or claims.get("company") or "").strip().lower()
    role = normalize_role(role_raw)

    if role not in {RoleEnum.sales_manager, RoleEnum.sales_executive, RoleEnum.admin}:
        return False

    if role != RoleEnum.admin and company_key and company_key != "exsol-engineering":
        return False

    return True


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_quantity(value: Any) -> int | None:
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None
    return quantity if quantity > 0 else None


def _split_serials(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        serials: list[str] = []
        for entry in value:
            serials.extend(_split_serials(entry))
        return serials
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r"[\s,]+", text)
    return [part.strip() for part in parts if part and part.strip()]


def _normalize_serial(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(int(value)).zfill(8)
    text = str(value).strip()
    if not text:
        return None
    if SERIAL_REGEX.match(text):
        return text
    return None


def _generate_serials(starting_serial: str, quantity: int, errors: list[str]) -> list[str]:
    normalized = _normalize_serial(starting_serial)
    if not normalized:
        errors.append("Starting serial must be exactly 8 digits.")
        return []

    start_int = int(normalized)
    end_int = start_int + quantity - 1
    if end_int > 99999999:
        errors.append("Serial range exceeds 8 digits. Adjust the starting serial or quantity.")
        return []

    return [str(start_int + offset).zfill(8) for offset in range(quantity)]


def _normalize_serial_mode(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"serialrange", "serial_range", "range"}:
        return "SerialRange"
    if text in {"manual", "list"}:
        return "Manual"
    return None


def _normalize_uuid(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError, TypeError):
        return None


def _serialize_entry(entry: ExsolProductionEntry, serial: ExsolProductionSerial, user_lookup: dict[int, str]):
    return {
        "id": entry.id,
        "company_key": entry.company_key,
        "production_date": entry.production_date.isoformat(),
        "item_code": entry.item_code,
        "item_name": entry.item_name,
        "serial_number": serial.serial_no,
        "production_shift": entry.shift,
        "created_by": entry.created_by_user_id,
        "created_by_name": entry.created_by_name or user_lookup.get(entry.created_by_user_id, "Unknown"),
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "is_confirmed": bool(entry.is_confirmed),
        "confirmed_by": entry.confirmed_by_user_id,
        "confirmed_at": entry.confirmed_at.isoformat() if entry.confirmed_at else None,
    }


def _load_user_lookup(user_ids: set[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    users = User.query.filter(User.id.in_(user_ids)).all()
    return {user.id: user.name for user in users}


def _ensure_exsol_sequences() -> None:
    global _EXSOL_SEQUENCES_READY
    if _EXSOL_SEQUENCES_READY:
        return
    bind = db.session.get_bind()
    if not bind or bind.dialect.name != "postgresql":
        return
    entries_seq = "exsol_production_entries_id_seq"
    serials_seq = "exsol_production_serials_id_seq"
    try:
        with bind.begin() as connection:
            connection.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {entries_seq}"))
            connection.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {serials_seq}"))
            connection.execute(
                text(
                    "ALTER TABLE exsol_production_entries "
                    f"ALTER COLUMN id SET DEFAULT nextval('{entries_seq}')"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE exsol_production_serials "
                    f"ALTER COLUMN id SET DEFAULT nextval('{serials_seq}')"
                )
            )
            connection.execute(
                text(
                    "SELECT setval("
                    f"'{entries_seq}', "
                    "GREATEST(COALESCE((SELECT MAX(id) FROM exsol_production_entries), 0), 1), "
                    "true)"
                )
            )
            connection.execute(
                text(
                    "SELECT setval("
                    f"'{serials_seq}', "
                    "GREATEST(COALESCE((SELECT MAX(id) FROM exsol_production_serials), 0), 1), "
                    "true)"
                )
            )
        _EXSOL_SEQUENCES_READY = True
    except SQLAlchemyError as exc:
        current_app.logger.warning(
            {
                "event": "exsol_sequence_setup_failed",
                "message": str(exc),
            }
        )


def _get_exsol_company_id() -> int | None:
    company = Company.query.filter(Company.name == EXSOL_COMPANY_NAME).one_or_none()
    return company.id if company else None


def _lookup_exsol_item(company_id: int, item_id: str | None, item_code: str | None) -> ExsolInventoryItem | None:
    if item_id:
        return (
            ExsolInventoryItem.query.filter(
                ExsolInventoryItem.company_id == company_id,
                ExsolInventoryItem.id == item_id,
                ExsolInventoryItem.is_active.is_(True),
            )
            .one_or_none()
        )
    if item_code:
        return (
            ExsolInventoryItem.query.filter(
                ExsolInventoryItem.company_id == company_id,
                ExsolInventoryItem.is_active.is_(True),
                func.lower(ExsolInventoryItem.item_code) == item_code.lower(),
            )
            .one_or_none()
        )
    return None


def _summarize_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    quantities = []
    serial_counts = []
    for row in rows:
        quantity = _parse_quantity(row.get("quantity"))
        if quantity:
            quantities.append(quantity)
        serials = row.get("serials") or row.get("serial_numbers")
        serial_list = _split_serials(serials)
        serial_counts.append(len(serial_list))
    return {
        "rows": len(rows),
        "quantity_total": sum(quantities),
        "serial_count_total": sum(serial_counts),
    }


def _log_bulk_failure(
    message: str,
    user_id: int | None,
    role_name: str | None,
    rows: list[dict[str, Any]],
    errors: list[dict[str, Any]] | None = None,
    serials: list[str] | None = None,
) -> None:
    serial_summary = None
    if serials:
        serial_summary = {
            "count": len(serials),
            "first": serials[0],
            "last": serials[-1],
        }
    current_app.logger.warning(
        {
            "event": "exsol_production_bulk_failure",
            "message": message,
            "user_id": user_id,
            "role": role_name,
            "payload": _summarize_payload(rows),
            "serial_summary": serial_summary,
            "errors": errors,
        }
    )


def _validate_bulk_rows(rows: list[dict[str, Any]], user_id: int, role_name: str, user_name: str):
    errors: dict[int, list[str]] = defaultdict(list)
    row_serials: dict[int, list[str]] = {}
    row_data: dict[int, dict[str, Any]] = {}

    company_id = _get_exsol_company_id()
    if not company_id:
        error_list = [
            {"row_index": idx, "messages": ["Exsol company is not configured."]}
            for idx in range(len(rows))
        ]
        raise ExsolProductionValidationError(error_list)

    item_ids = {
        normalized
        for row in rows
        for normalized in (_normalize_uuid(row.get("item_id")),)
        if normalized
    }
    item_codes = {
        (row.get("item_code") or "").strip()
        for row in rows
        if (row.get("item_code") or "").strip()
    }
    filters = []
    if item_ids:
        filters.append(ExsolInventoryItem.id.in_(item_ids))
    if item_codes:
        filters.append(func.lower(ExsolInventoryItem.item_code).in_({code.lower() for code in item_codes}))
    items: list[ExsolInventoryItem] = []
    if filters:
        items = (
            ExsolInventoryItem.query.filter(
                ExsolInventoryItem.company_id == company_id,
                ExsolInventoryItem.is_active.is_(True),
            )
            .filter(or_(*filters))
            .all()
        )
    item_lookup_by_code = {item.item_code.lower(): item for item in items}
    item_lookup_by_id = {str(item.id): item for item in items}

    for idx, row in enumerate(rows):
        production_date = _parse_date(row.get("production_date"))
        if not production_date:
            errors[idx].append("Production date is required.")

        item_id_raw = (row.get("item_id") or "").strip()
        item_id = _normalize_uuid(item_id_raw) or ""
        item_code = (row.get("item_code") or "").strip()
        if not item_id and not item_code:
            errors[idx].append("Item code is required.")
            item = None
        else:
            item = item_lookup_by_id.get(item_id) if item_id else item_lookup_by_code.get(item_code.lower())
            if not item:
                label = item_code or item_id
                errors[idx].append(f"Item {label} is not an Exsol inventory item.")

        shift = (row.get("shift") or row.get("production_shift") or "").strip()
        if shift and shift not in SHIFT_OPTIONS:
            errors[idx].append("Production shift must be Morning, Evening, or Night.")

        quantity = _parse_quantity(row.get("quantity"))
        if quantity is not None and quantity > MAX_BULK_QUANTITY:
            errors[idx].append(f"Quantity cannot exceed {MAX_BULK_QUANTITY}.")

        serial_mode = _normalize_serial_mode(row.get("serial_mode"))
        if not serial_mode:
            errors[idx].append("Serial mode must be SerialRange or Manual.")

        serials: list[str] = []
        if serial_mode == "Manual":
            raw_serials = row.get("serials") or row.get("serial_numbers")
            serials = [_normalize_serial(value) or "" for value in _split_serials(raw_serials)]
            invalid_serials = [value for value in serials if not SERIAL_REGEX.match(value)]
            serials = [value for value in serials if SERIAL_REGEX.match(value)]
            if invalid_serials:
                errors[idx].append("All serial numbers must be exactly 8 digits.")
            if not serials:
                errors[idx].append("Serial numbers are required for manual entry.")
            if quantity is None:
                quantity = len(serials)
            elif quantity and len(serials) != quantity:
                errors[idx].append("Quantity must match the number of serials provided.")
            if quantity and quantity > MAX_BULK_QUANTITY:
                errors[idx].append(f"Quantity cannot exceed {MAX_BULK_QUANTITY}.")
        elif serial_mode == "SerialRange":
            starting_serial = row.get("start_serial") or row.get("starting_serial")
            if not starting_serial:
                errors[idx].append("Starting serial is required for serial range mode.")
            if quantity is None:
                errors[idx].append("Quantity must be provided to generate serials.")
            else:
                serials = _generate_serials(str(starting_serial or ""), quantity, errors[idx])

        if quantity is None:
            errors[idx].append("Quantity must be a positive integer.")

        if errors[idx]:
            continue

        row_serials[idx] = serials
        row_data[idx] = {
            "production_date": production_date,
            "item_code": item.item_code if item else item_code,
            "item_name": item.item_name if item else (row.get("item_name") or item_code),
            "shift": shift or None,
            "quantity": quantity,
            "serial_mode": serial_mode,
            "created_by_user_id": user_id,
            "created_by_name": user_name or None,
            "created_role": role_name,
        }

    serial_to_rows: dict[str, list[int]] = defaultdict(list)
    for idx, serials in row_serials.items():
        seen_serials: set[str] = set()
        for serial in serials:
            serial_to_rows[serial].append(idx)
            if serial in seen_serials:
                errors[idx].append(f"Serial {serial} is duplicated in this row.")
            seen_serials.add(serial)

    for serial, row_indexes in serial_to_rows.items():
        if len(row_indexes) > 1:
            for row_idx in row_indexes:
                errors[row_idx].append(f"Serial {serial} is duplicated in this batch.")

    existing_lookup: dict[str, tuple[date | None, int | None]] = {}
    if serial_to_rows:
        existing_serials = (
            db.session.query(
                ExsolProductionSerial.serial_no,
                ExsolProductionEntry.production_date,
                ExsolProductionEntry.created_by_user_id,
            )
            .join(ExsolProductionEntry, ExsolProductionSerial.entry_id == ExsolProductionEntry.id)
            .filter(ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY)
            .filter(ExsolProductionSerial.serial_no.in_(list(serial_to_rows.keys())))
            .all()
        )
        for serial_no, production_date, created_by in existing_serials:
            existing_lookup[serial_no] = (production_date, created_by)

    if existing_lookup:
        user_lookup = _load_user_lookup({user_id for _, user_id in existing_lookup.values() if user_id})
        for idx, serials in row_serials.items():
            conflicts = [serial for serial in serials if serial in existing_lookup]
            if not conflicts:
                continue
            messages = []
            for serial in conflicts[:10]:
                used_on, created_by = existing_lookup[serial]
                used_label = used_on.isoformat() if used_on else "unknown date"
                user_name = user_lookup.get(created_by, "Unknown")
                messages.append(f"Serial {serial} already exists (used on {used_label} by {user_name}).")
            if len(conflicts) > 10:
                messages.append(f"and {len(conflicts) - 10} more…")
            errors[idx].extend(messages)

    if any(errors.values()):
        error_list = [
            {"row_index": idx, "messages": msgs}
            for idx, msgs in sorted(errors.items())
            if msgs
        ]
        raise ExsolProductionValidationError(error_list)

    entries: list[ExsolProductionEntry] = []
    serials_created: list[str] = []
    for idx, serials in row_serials.items():
        data = row_data[idx]
        entry = ExsolProductionEntry(
            company_key=EXSOL_COMPANY_KEY,
            production_date=data["production_date"],
            item_code=data["item_code"],
            item_name=data["item_name"],
            shift=data["shift"],
            quantity=data["quantity"],
            serial_mode=data["serial_mode"],
            created_by_user_id=data["created_by_user_id"],
            created_by_name=data["created_by_name"],
        )
        for serial in serials:
            entry.serials.append(
                ExsolProductionSerial(company_key=EXSOL_COMPANY_KEY, serial_no=serial)
            )
            serials_created.append(serial)
        entries.append(entry)

    return entries, serials_created


@bp.get("/entries")
@jwt_required()
def list_entries():
    if not _has_exsol_production_access():
        return _build_error("Access denied", 403)

    params = request.args
    start_date = _parse_date(params.get("date_from") or params.get("start_date"))
    end_date = _parse_date(params.get("date_to") or params.get("end_date"))
    item_code = (params.get("item_code") or "").strip()
    serial_no = (params.get("serial_no") or "").strip()
    production_shift = (params.get("production_shift") or "").strip()
    created_by = params.get("created_by")
    confirmed = (params.get("confirmed") or "").strip().lower()
    try:
        limit = int(params.get("limit", 250))
    except (TypeError, ValueError):
        return _build_error("Invalid limit parameter.", 400)
    limit = min(max(limit, 1), 500)

    query = (
        db.session.query(ExsolProductionEntry, ExsolProductionSerial)
        .join(ExsolProductionSerial, ExsolProductionSerial.entry_id == ExsolProductionEntry.id)
        .filter(ExsolProductionEntry.company_key == EXSOL_COMPANY_KEY)
    )

    if start_date:
        query = query.filter(ExsolProductionEntry.production_date >= start_date)
    if end_date:
        query = query.filter(ExsolProductionEntry.production_date <= end_date)
    if item_code:
        query = query.filter(func.lower(ExsolProductionEntry.item_code) == item_code.lower())
    if serial_no:
        query = query.filter(ExsolProductionSerial.serial_no == serial_no)
    if production_shift:
        query = query.filter(ExsolProductionEntry.shift == production_shift)
    if created_by:
        try:
            created_id = int(created_by)
            query = query.filter(ExsolProductionEntry.created_by_user_id == created_id)
        except (TypeError, ValueError):
            return _build_error("Invalid created_by filter", 400)
    if confirmed in {"true", "false"}:
        query = query.filter(ExsolProductionEntry.is_confirmed == (confirmed == "true"))

    rows = (
        query.order_by(
            ExsolProductionEntry.production_date.desc(),
            ExsolProductionEntry.created_at.desc(),
            ExsolProductionSerial.serial_no.asc(),
        )
        .limit(limit)
        .all()
    )
    user_lookup = _load_user_lookup({entry.created_by_user_id for entry, _ in rows})
    return jsonify([_serialize_entry(entry, serial, user_lookup) for entry, serial in rows])


@bp.post("/bulk")
@jwt_required()
def bulk_create_entries():
    if not _has_exsol_production_access():
        return _build_error("Access denied", 403)

    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return _build_error("Rows payload is required.", 400)

    user_id_raw = get_jwt_identity()
    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        return _build_error("Invalid user identity.", 403)

    claims = get_jwt()
    role_name = (claims.get("role") or "").strip()
    user = User.query.get(user_id)
    user_name = user.name if user else ""

    try:
        entries, serials_created = _validate_bulk_rows(rows, user_id, role_name, user_name)
    except ExsolProductionValidationError as exc:
        _log_bulk_failure("validation_error", user_id, role_name, rows, errors=exc.errors)
        return jsonify({"ok": False, "errors": exc.errors}), 400
    except SQLAlchemyError:
        _log_bulk_failure("database_error", user_id, role_name, rows)
        return _build_error("Unable to save production entries right now.", 500)

    try:
        _ensure_exsol_sequences()
        for entry in entries:
            db.session.add(entry)
        db.session.commit()
        entry_ids = [entry.id for entry in entries]
    except IntegrityError:
        db.session.rollback()
        _log_bulk_failure("unique_constraint_violation", user_id, role_name, rows, serials=serials_created)
        return _build_error(
            "One or more serials already exist. Please refresh and try again.",
            400,
        )
    except SQLAlchemyError:
        db.session.rollback()
        _log_bulk_failure("database_error", user_id, role_name, rows, serials=serials_created)
        return _build_error("Unable to save production entries right now.", 500)

    summary = {
        "rows_processed": len(entries),
        "total_quantity": sum(entry.quantity for entry in entries),
        "total_serials": len(serials_created),
    }
    return jsonify(
        {
            "ok": True,
            "entry_ids": entry_ids,
            "created_count": len(entries),
            "serials_created": serials_created,
            "summary": summary,
        }
    )


@bp.post("/confirm")
@jwt_required()
def confirm_entries():
    if not _has_exsol_production_access():
        return _build_error("Access denied", 403)

    payload = request.get_json(silent=True) or {}
    entry_ids = payload.get("entry_ids") or []
    if not isinstance(entry_ids, list) or not entry_ids:
        return _build_error("entry_ids is required.", 400)

    user_id_raw = get_jwt_identity()
    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        return _build_error("Invalid user identity.", 403)

    entries = (
        ExsolProductionEntry.query.filter(ExsolProductionEntry.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolProductionEntry.id.in_(entry_ids))
        .all()
    )
    if not entries:
        return _build_error("No matching entries found.", 404)

    now = datetime.utcnow()
    updated = 0
    try:
        for entry in entries:
            if entry.is_confirmed:
                continue
            entry.is_confirmed = True
            entry.confirmed_by_user_id = user_id
            entry.confirmed_at = now
            updated += 1
            db.session.add(entry)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _build_error("Unable to confirm entries right now.", 500)

    return jsonify({"ok": True, "confirmed": updated})


@bp.patch("/entries/<int:entry_id>")
@jwt_required()
def update_entry(entry_id: int):
    if not _has_exsol_production_access():
        return _build_error("Access denied", 403)

    payload = request.get_json(silent=True) or {}

    entry = (
        ExsolProductionEntry.query.filter(ExsolProductionEntry.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolProductionEntry.id == entry_id)
        .one_or_none()
    )
    if not entry:
        return _build_error("Entry not found.", 404)
    if entry.is_confirmed:
        return _build_error("Confirmed entries cannot be edited.", 400)
    if entry.production_date != date.today():
        return _build_error("Entries can only be edited on the production date.", 400)

    updates: dict[str, Any] = {}
    if "production_shift" in payload or "shift" in payload:
        production_shift = (payload.get("shift") or payload.get("production_shift") or "").strip()
        if production_shift and production_shift not in SHIFT_OPTIONS:
            return _build_error("Production shift must be Morning, Evening, or Night.", 400)
        updates["shift"] = production_shift or None

    if "item_code" in payload or "item_id" in payload:
        item_id = (payload.get("item_id") or "").strip()
        item_code = (payload.get("item_code") or "").strip()
        if not item_id and not item_code:
            return _build_error("Item code is required.", 400)
        company_id = _get_exsol_company_id()
        if not company_id:
            return _build_error("Exsol company not configured.", 500)
        item = _lookup_exsol_item(company_id, item_id, item_code)
        if not item:
            return _build_error("Item is not an Exsol inventory item.", 400)
        updates["item_code"] = item.item_code
        updates["item_name"] = item.item_name

    if "production_date" in payload:
        production_date = _parse_date(payload.get("production_date"))
        if not production_date or production_date != date.today():
            return _build_error("Production date can only be set to today.", 400)
        updates["production_date"] = production_date

    if not updates:
        return _build_error("No valid fields to update.", 400)

    try:
        for key, value in updates.items():
            setattr(entry, key, value)
        db.session.add(entry)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _build_error("Unable to update entry right now.", 500)

    user_lookup = _load_user_lookup({entry.created_by_user_id})
    serial = entry.serials[0] if entry.serials else ExsolProductionSerial(serial_no="00000000")
    return jsonify({"ok": True, "entry": _serialize_entry(entry, serial, user_lookup)})


@bp.get("/template")
@jwt_required()
def download_template():
    if not _has_exsol_production_access():
        return _build_error("Access denied", 403)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Exsol Production"
    sheet.append(
        [
            "production_date",
            "item_code",
            "quantity",
            "production_shift",
            "remarks",
            "starting_serial",
            "serial_numbers",
        ]
    )
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="exsol_production_template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.post("/excel")
@jwt_required()
def upload_excel():
    if not _has_exsol_production_access():
        return _build_error("Access denied", 403)

    if "file" not in request.files:
        return _build_error("Excel file is required.", 400)
    file = request.files["file"]
    if not file or file.filename == "":
        return _build_error("Excel file is required.", 400)

    try:
        workbook = load_workbook(file, data_only=True)
    except Exception:
        return _build_error("Unable to read the uploaded Excel file.", 400)

    sheet = workbook.active
    try:
        header_cells = next(sheet.iter_rows(max_row=1))
    except StopIteration:
        return _build_error("Excel template headers are missing.", 400)
    header_row = [str(cell.value or "").strip().lower() for cell in header_cells]
    header_map = {header: idx for idx, header in enumerate(header_row) if header}

    required_headers = {"production_date", "item_code", "quantity", "production_shift"}
    if not required_headers.issubset(header_map.keys()):
        return _build_error("Excel template headers are missing.", 400)

    rows: list[dict[str, Any]] = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not any(value not in (None, "") for value in row):
            continue
        payload: dict[str, Any] = {}
        for header, idx in header_map.items():
            payload[header] = row[idx] if idx < len(row) else None
        serial_numbers = payload.get("serial_numbers")
        starting_serial = payload.get("starting_serial")
        if serial_numbers:
            payload["serial_mode"] = "Manual"
            payload["serials"] = serial_numbers
        else:
            payload["serial_mode"] = "SerialRange"
            payload["start_serial"] = starting_serial
        rows.append(payload)

    if not rows:
        return _build_error("Excel file contains no production rows.", 400)

    user_id_raw = get_jwt_identity()
    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        return _build_error("Invalid user identity.", 403)

    claims = get_jwt()
    role_name = (claims.get("role") or "").strip()
    user = User.query.get(user_id)
    user_name = user.name if user else ""

    try:
        entries, serials_created = _validate_bulk_rows(rows, user_id, role_name, user_name)
    except ExsolProductionValidationError as exc:
        _log_bulk_failure("validation_error", user_id, role_name, rows, errors=exc.errors)
        return jsonify({"ok": False, "errors": exc.errors}), 400
    except SQLAlchemyError:
        _log_bulk_failure("database_error", user_id, role_name, rows)
        return _build_error("Unable to save production entries right now.", 500)

    try:
        _ensure_exsol_sequences()
        for entry in entries:
            db.session.add(entry)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _log_bulk_failure("unique_constraint_violation", user_id, role_name, rows, serials=serials_created)
        return _build_error(
            "One or more serials already exist. Please refresh and try again.",
            400,
        )
    except SQLAlchemyError:
        db.session.rollback()
        _log_bulk_failure("database_error", user_id, role_name, rows, serials=serials_created)
        return _build_error("Unable to save production entries right now.", 500)

    summary = {
        "rows_processed": len(entries),
        "total_quantity": sum(entry.quantity for entry in entries),
        "total_serials": len(serials_created),
    }

    return jsonify(
        {
            "ok": True,
            "entry_ids": [entry.id for entry in entries],
            "created_count": len(entries),
            "serials_created": serials_created,
            "summary": summary,
        }
    )
