from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import io
import re
from typing import Any

from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from openpyxl import Workbook, load_workbook
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from exsol_storage import (
    ExsolProductionEntry,
    ExsolStockItem,
    ExsolStorageUnavailable,
    get_exsol_storage,
)
from models import RoleEnum, User


bp = Blueprint("exsol_production", __name__, url_prefix="/api/exsol/production")

EXSOL_COMPANY_NAME = "Exsol Engineering (Pvt) Ltd"
SHIFT_OPTIONS = {"Morning", "Evening", "Night"}
SERIAL_REGEX = re.compile(r"^[0-9]{8}$")


@dataclass
class ExsolProductionValidationError(Exception):
    errors: list[dict[str, Any]]

    def __str__(self) -> str:  # pragma: no cover - utility
        return "Exsol production validation error"


def _build_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _has_exsol_production_access() -> bool:
    try:
        claims = get_jwt()
    except Exception:
        return False

    role_raw = claims.get("role")
    try:
        role = RoleEnum(role_raw)
    except Exception:
        role = None

    if role not in {RoleEnum.sales_manager, RoleEnum.sales_executive}:
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


def _serialize_entry(entry: ExsolProductionEntry, user_lookup: dict[int, str]):
    return {
        "id": str(entry.id),
        "company": entry.company,
        "production_date": entry.production_date.isoformat(),
        "item_code": entry.item_code,
        "item_name": entry.item_name,
        "serial_number": entry.serial_number,
        "production_shift": entry.production_shift,
        "remarks": entry.remarks,
        "created_by": entry.created_by,
        "created_by_name": user_lookup.get(entry.created_by, "Unknown"),
        "created_role": entry.created_role,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "is_confirmed": bool(entry.is_confirmed),
        "confirmed_by": entry.confirmed_by,
        "confirmed_at": entry.confirmed_at.isoformat() if entry.confirmed_at else None,
    }


def _load_user_lookup(user_ids: set[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    users = User.query.filter(User.id.in_(user_ids)).all()
    return {user.id: user.name for user in users}


def _coerce_serial_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value)).zfill(8)
    text = str(value).strip()
    return text or None


def _normalize_row_payload(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    if "starting_serial" in normalized:
        normalized["starting_serial"] = _coerce_serial_text(normalized.get("starting_serial"))
    if "serial_numbers" in normalized:
        normalized["serial_numbers"] = _coerce_serial_text(normalized.get("serial_numbers"))
    return normalized


def _validate_and_build_entries(rows: list[dict[str, Any]], user_id: int, role_name: str):
    storage = get_exsol_storage()
    session = storage.session()
    errors: dict[int, list[str]] = defaultdict(list)
    row_serials: dict[int, list[str]] = {}
    row_quantities: dict[int, int] = {}
    row_data: dict[int, dict[str, Any]] = {}
    entries: list[ExsolProductionEntry] = []

    try:
        item_codes = {
            (row.get("item_code") or "").strip()
            for row in rows
            if (row.get("item_code") or "").strip()
        }
        items = (
            session.query(ExsolStockItem)
            .filter(func.lower(ExsolStockItem.item_code).in_({code.lower() for code in item_codes}))
            .all()
        )
        item_lookup = {item.item_code.lower(): item for item in items}

        for idx, raw_row in enumerate(rows):
            row = _normalize_row_payload(raw_row)
            production_date = _parse_date(row.get("production_date"))
            if not production_date:
                errors[idx].append("Production date is required.")

            item_code = (row.get("item_code") or "").strip()
            if not item_code:
                errors[idx].append("Item code is required.")
                item = None
            else:
                item = item_lookup.get(item_code.lower())
                if not item:
                    errors[idx].append(f"Item code {item_code} is not an Exsol stock item.")

            production_shift = (row.get("production_shift") or "").strip()
            if production_shift not in SHIFT_OPTIONS:
                errors[idx].append("Production shift must be Morning, Evening, or Night.")

            quantity = _parse_quantity(row.get("quantity"))
            if quantity is None:
                errors[idx].append("Quantity must be a positive integer.")

            serial_mode = (row.get("serial_mode") or row.get("serial_input_mode") or "").strip().lower()
            serial_numbers_raw = row.get("serial_numbers")
            starting_serial = row.get("starting_serial")

            use_manual_list = bool(serial_numbers_raw) or serial_mode in {"manual", "list"}
            serials: list[str] = []
            if quantity is None:
                quantity = 0

            if use_manual_list:
                serials = [_normalize_serial(value) or "" for value in _split_serials(serial_numbers_raw)]
                invalid_serials = [value for value in serials if not SERIAL_REGEX.match(value)]
                serials = [value for value in serials if SERIAL_REGEX.match(value)]
                if invalid_serials:
                    errors[idx].append("All serial numbers must be exactly 8 digits.")
                if quantity and len(serials) != quantity:
                    errors[idx].append("Quantity must match the number of serials provided.")
                if not serials:
                    errors[idx].append("Serial numbers are required for manual entry.")
            else:
                if not starting_serial:
                    errors[idx].append("Starting serial is required for serial range mode.")
                if quantity:
                    serials = _generate_serials(str(starting_serial or ""), quantity, errors[idx])
                else:
                    errors[idx].append("Quantity must be provided to generate serials.")

            if errors[idx]:
                continue

            row_serials[idx] = serials
            row_quantities[idx] = quantity
            row_data[idx] = {
                "production_date": production_date,
                "item_code": item.item_code if item else item_code,
                "item_name": item.item_name if item else (row.get("item_name") or item_code),
                "production_shift": production_shift,
                "remarks": (row.get("remarks") or "").strip() or None,
            }

        serial_to_rows: dict[str, list[int]] = defaultdict(list)
        for idx, serials in row_serials.items():
            for serial in serials:
                serial_to_rows[serial].append(idx)

        for serial, row_indexes in serial_to_rows.items():
            if len(row_indexes) > 1:
                for row_idx in row_indexes:
                    errors[row_idx].append(f"Serial {serial} is duplicated in this batch.")

        if serial_to_rows:
            existing_entries = (
                session.query(ExsolProductionEntry)
                .filter(ExsolProductionEntry.company == EXSOL_COMPANY_NAME)
                .filter(ExsolProductionEntry.serial_number.in_(list(serial_to_rows.keys())))
                .all()
            )
        else:
            existing_entries = []

        existing_lookup = {entry.serial_number: entry for entry in existing_entries}
        if existing_lookup:
            user_lookup = _load_user_lookup({entry.created_by for entry in existing_entries})
            for idx, serials in row_serials.items():
                conflicts = [serial for serial in serials if serial in existing_lookup]
                if not conflicts:
                    continue
                messages = []
                for serial in conflicts[:10]:
                    entry = existing_lookup[serial]
                    used_on = entry.production_date.isoformat() if entry.production_date else "unknown date"
                    user_name = user_lookup.get(entry.created_by, "Unknown")
                    messages.append(
                        f"Serial {serial} already exists (used on {used_on} by {user_name})."
                    )
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

        for idx, serials in row_serials.items():
            data = row_data[idx]
            for serial in serials:
                entries.append(
                    ExsolProductionEntry(
                        company=EXSOL_COMPANY_NAME,
                        production_date=data["production_date"],
                        item_code=data["item_code"],
                        item_name=data["item_name"],
                        serial_number=serial,
                        production_shift=data["production_shift"],
                        remarks=data["remarks"],
                        created_by=user_id,
                        created_role=role_name,
                    )
                )

        with session.begin():
            session.add_all(entries)

        summary = {
            "rows_processed": len(rows),
            "total_quantity": sum(row_quantities.values()),
            "total_serials": len(entries),
        }
        return summary
    finally:
        try:
            session.close()
        except Exception:
            pass


@bp.get("/entries")
@jwt_required()
def list_entries():
    if not _has_exsol_production_access():
        return _build_error("Access denied", 403)

    params = request.args
    start_date = _parse_date(params.get("start_date"))
    end_date = _parse_date(params.get("end_date"))
    item_code = (params.get("item_code") or "").strip()
    production_shift = (params.get("production_shift") or "").strip()
    created_by = params.get("created_by")
    confirmed = (params.get("confirmed") or "").strip().lower()
    try:
        limit = int(params.get("limit", 250))
    except (TypeError, ValueError):
        return _build_error("Invalid limit parameter.", 400)
    limit = min(max(limit, 1), 500)

    try:
        storage = get_exsol_storage()
    except ExsolStorageUnavailable as exc:
        return _build_error(str(exc), 503)

    session = storage.session()

    try:
        query = session.query(ExsolProductionEntry).filter(
            ExsolProductionEntry.company == EXSOL_COMPANY_NAME
        )

        if start_date:
            query = query.filter(ExsolProductionEntry.production_date >= start_date)
        if end_date:
            query = query.filter(ExsolProductionEntry.production_date <= end_date)
        if item_code:
            query = query.filter(
                func.lower(ExsolProductionEntry.item_code) == item_code.lower()
            )
        if production_shift:
            query = query.filter(ExsolProductionEntry.production_shift == production_shift)
        if created_by:
            try:
                created_id = int(created_by)
                query = query.filter(ExsolProductionEntry.created_by == created_id)
            except (TypeError, ValueError):
                return _build_error("Invalid created_by filter", 400)
        if confirmed in {"true", "false"}:
            query = query.filter(ExsolProductionEntry.is_confirmed == (confirmed == "true"))

        entries = (
            query.order_by(
                ExsolProductionEntry.production_date.desc(),
                ExsolProductionEntry.created_at.desc(),
            )
            .limit(limit)
            .all()
        )
        user_lookup = _load_user_lookup({entry.created_by for entry in entries})
        return jsonify([_serialize_entry(entry, user_lookup) for entry in entries])
    except ExsolStorageUnavailable as exc:
        return _build_error(str(exc), 503)
    finally:
        try:
            session.close()
        except Exception:
            pass


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

    try:
        summary = _validate_and_build_entries(rows, user_id, role_name)
    except ExsolProductionValidationError as exc:
        return jsonify({"ok": False, "errors": exc.errors}), 400
    except ExsolStorageUnavailable as exc:
        return _build_error(str(exc), 503)
    except SQLAlchemyError:
        return _build_error("Unable to save production entries right now.", 500)
    return jsonify({"ok": True, "summary": summary})


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

    try:
        storage = get_exsol_storage()
    except ExsolStorageUnavailable as exc:
        return _build_error(str(exc), 503)

    session = storage.session()

    try:
        entries = (
            session.query(ExsolProductionEntry)
            .filter(ExsolProductionEntry.company == EXSOL_COMPANY_NAME)
            .filter(ExsolProductionEntry.id.in_(entry_ids))
            .all()
        )
        if not entries:
            return _build_error("No matching entries found.", 404)

        now = datetime.utcnow()
        updated = 0
        with session.begin():
            for entry in entries:
                if entry.is_confirmed:
                    continue
                entry.is_confirmed = True
                entry.confirmed_by = user_id
                entry.confirmed_at = now
                updated += 1
                session.add(entry)

        return jsonify({"ok": True, "confirmed": updated})
    except ExsolStorageUnavailable as exc:
        return _build_error(str(exc), 503)
    except SQLAlchemyError:
        return _build_error("Unable to confirm entries right now.", 500)
    finally:
        try:
            session.close()
        except Exception:
            pass


@bp.patch("/entries/<entry_id>")
@jwt_required()
def update_entry(entry_id: str):
    if not _has_exsol_production_access():
        return _build_error("Access denied", 403)

    payload = request.get_json(silent=True) or {}
    storage = get_exsol_storage()
    session = storage.session()

    try:
        entry = (
            session.query(ExsolProductionEntry)
            .filter(ExsolProductionEntry.company == EXSOL_COMPANY_NAME)
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
        if "production_shift" in payload:
            production_shift = (payload.get("production_shift") or "").strip()
            if production_shift not in SHIFT_OPTIONS:
                return _build_error("Production shift must be Morning, Evening, or Night.", 400)
            updates["production_shift"] = production_shift

        if "remarks" in payload:
            updates["remarks"] = (payload.get("remarks") or "").strip() or None

        if "item_code" in payload:
            item_code = (payload.get("item_code") or "").strip()
            if not item_code:
                return _build_error("Item code is required.", 400)
            item = (
                session.query(ExsolStockItem)
                .filter(func.lower(ExsolStockItem.item_code) == item_code.lower())
                .one_or_none()
            )
            if not item:
                return _build_error("Item code is not an Exsol stock item.", 400)
            updates["item_code"] = item.item_code
            updates["item_name"] = item.item_name

        if "production_date" in payload:
            production_date = _parse_date(payload.get("production_date"))
            if not production_date or production_date != date.today():
                return _build_error("Production date can only be set to today.", 400)
            updates["production_date"] = production_date

        if not updates:
            return _build_error("No valid fields to update.", 400)

        with session.begin():
            for key, value in updates.items():
                setattr(entry, key, value)
            session.add(entry)

        user_lookup = _load_user_lookup({entry.created_by})
        return jsonify({"ok": True, "entry": _serialize_entry(entry, user_lookup)})
    except ExsolStorageUnavailable as exc:
        return _build_error(str(exc), 503)
    except SQLAlchemyError:
        return _build_error("Unable to update entry right now.", 500)
    finally:
        try:
            session.close()
        except Exception:
            pass


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

    try:
        summary = _validate_and_build_entries(rows, user_id, role_name)
    except ExsolProductionValidationError as exc:
        return jsonify({"ok": False, "errors": exc.errors}), 400
    except ExsolStorageUnavailable as exc:
        return _build_error(str(exc), 503)
    except SQLAlchemyError:
        return _build_error("Unable to save production entries right now.", 500)

    return jsonify({"ok": True, "summary": summary})
