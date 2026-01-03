from __future__ import annotations

from __future__ import annotations

import csv
import io
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from flask import Blueprint, Response, current_app, jsonify, request, send_file
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Company, NonSamproxCustomer, RoleEnum, User
from routes import non_samprox_customers as nsc

bp = Blueprint("dealers", __name__, url_prefix="/api/dealers")

ALLOWED_BULK_ROLES = {RoleEnum.admin, RoleEnum.finance_manager, RoleEnum.production_manager}
REQUIRED_COLUMNS = ["customer_name", "area_code", "city", "district", "province", "managed_by"]
OPTIONAL_COLUMNS = ["customer_code"]
TEMPLATE_COLUMNS = OPTIONAL_COLUMNS + REQUIRED_COLUMNS


def _current_role() -> Optional[RoleEnum]:
    claims = get_jwt() or {}
    try:
        return RoleEnum(claims.get("role"))
    except Exception:
        return None


def _current_user() -> Optional[User]:
    identity = get_jwt_identity()
    if isinstance(identity, dict):
        for key in ("id", "user_id", "sub"):
            if identity.get(key) is not None:
                try:
                    return User.query.get(int(identity.get(key)))
                except (TypeError, ValueError):
                    return None
    try:
        return User.query.get(int(identity))
    except (TypeError, ValueError):
        return None


def _guard_bulk_roles():
    role = _current_role()
    if role not in ALLOWED_BULK_ROLES:
        return jsonify({"ok": False, "error": "Access denied"}), 403
    return None


def _normalize_header(name: str) -> str:
    return (name or "").strip().lower()


def _normalize_value(value: object) -> str:
    return (str(value) if value is not None else "").strip()


def _load_company() -> tuple[Optional[Company], Optional[tuple[Response, int]]]:
    company_raw = request.form.get("company_id") or request.args.get("company_id") or None
    try:
        company_id = int(company_raw) if company_raw is not None else None
    except (TypeError, ValueError):
        return None, (jsonify({"ok": False, "error": "Invalid company_id"}), 400)

    if company_id is None:
        return None, (jsonify({"ok": False, "error": "company_id is required"}), 400)

    company = Company.query.get(company_id)
    if not company:
        return None, (jsonify({"ok": False, "error": "Company not found"}), 404)
    return company, None


@dataclass
class ParsedRow:
    row_number: int
    data: dict[str, str]
    error: Optional[str] = None
    managed_by_user_id: Optional[int] = None
    provided_code: Optional[str] = None
    managed_by_name: Optional[str] = None


def _read_csv(file_storage) -> tuple[list[ParsedRow], list[str]]:
    if not file_storage:
        return [], ["CSV file is required"]

    try:
        content = file_storage.stream.read().decode("utf-8-sig")
    except Exception:
        return [], ["Unable to read CSV file; please ensure it is UTF-8 encoded"]

    reader = csv.DictReader(io.StringIO(content))
    headers = [_normalize_header(h) for h in (reader.fieldnames or [])]

    missing_required = [col for col in REQUIRED_COLUMNS if col not in headers]
    if missing_required:
        return [], [f"Missing required columns: {', '.join(missing_required)}"]

    rows: list[ParsedRow] = []
    for idx, raw in enumerate(reader, start=2):  # include header offset
        normalized = {_normalize_header(k): _normalize_value(v) for k, v in (raw or {}).items()}
        rows.append(ParsedRow(row_number=idx, data=normalized))
    return rows, []


def _resolve_managed_by(value: str, users: list[User]) -> Optional[User]:
    if not value:
        return None
    try:
        candidate_id = int(value)
    except (TypeError, ValueError):
        candidate_id = None

    lower_value = value.lower()

    for user in users:
        if candidate_id is not None and user.id == candidate_id:
            return user
        if user.name and user.name.lower() == lower_value:
            return user
        if user.email and user.email.lower() == lower_value:
            return user
    return None


def _validate_rows(company: Company, parsed_rows: list[ParsedRow]) -> tuple[list[ParsedRow], list[ParsedRow]]:
    active_users = User.query.filter(User.active.is_(True)).all()
    existing_codes = {
        c[0]
        for c in db.session.query(NonSamproxCustomer.customer_code)
        .filter(NonSamproxCustomer.customer_code != None)  # noqa: E711
        .all()
    }
    seen_codes: set[str] = set()

    for row in parsed_rows:
        data = row.data
        missing_values = [col for col in REQUIRED_COLUMNS if not data.get(col)]
        if missing_values:
            row.error = f"Missing required values: {', '.join(missing_values)}"
            continue

        managed_by_value = data.get("managed_by") or ""
        manager = _resolve_managed_by(managed_by_value, active_users)
        if not manager:
            row.error = "managed_by does not match any active user (id, name, or email)"
            continue

        customer_code = data.get("customer_code") or None
        if customer_code:
            if not nsc._validate_customer_code(company, customer_code):
                row.error = "Invalid customer_code format for company"
                continue
            if customer_code in seen_codes:
                row.error = "Duplicate customer_code within file"
                continue
            if customer_code in existing_codes:
                row.error = "customer_code already exists"
                continue
            seen_codes.add(customer_code)
            row.provided_code = customer_code

        row.managed_by_user_id = manager.id
        row.managed_by_name = manager.name

    valid = [r for r in parsed_rows if not r.error]
    failed = [r for r in parsed_rows if r.error]
    return valid, failed


def _serialize_rows(rows: Iterable[ParsedRow]) -> list[dict[str, Any]]:
    serialized = []
    for row in rows:
        payload = {key: row.data.get(key) for key in TEMPLATE_COLUMNS}
        payload["row_number"] = row.row_number
        payload["error"] = row.error
        serialized.append(payload)
    return serialized


def _generate_error_report(rows: Iterable[ParsedRow]) -> tuple[Optional[str], Optional[str]]:
    rows_list = list(rows)
    if not rows_list:
        return None, None

    token = uuid.uuid4().hex
    instance_dir = Path(current_app.instance_path or ".")
    target_dir = instance_dir / "dealer_error_reports"
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{token}.csv"

    with file_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TEMPLATE_COLUMNS + ["error", "row_number"])
        writer.writeheader()
        for row in rows_list:
            serialized = {key: row.data.get(key) for key in TEMPLATE_COLUMNS}
            serialized["error"] = row.error
            serialized["row_number"] = row.row_number
            writer.writerow(serialized)

    return token, str(file_path)


@bp.get("")
@jwt_required()
def list_dealers():
    customers = NonSamproxCustomer.query.order_by(NonSamproxCustomer.customer_code.asc()).all()
    data = [
        {
            "id": str(customer.id),
            "customer_code": customer.customer_code,
            "customer_name": customer.customer_name,
            "area_code": customer.area_code,
            "city": customer.city,
            "district": customer.district,
            "province": customer.province,
            "managed_by": customer.managed_by_label or getattr(customer.managed_by, "name", None),
            "company": customer.company_label or getattr(customer.company, "name", None),
        }
        for customer in customers
    ]
    return jsonify(data)


@bp.get("/bulk-template")
@jwt_required()
def bulk_template():
    guard = _guard_bulk_roles()
    if guard:
        return guard

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=TEMPLATE_COLUMNS)
    writer.writeheader()
    writer.writerow(
        {
            "customer_code": "",
            "customer_name": "Dealer name",
            "area_code": "123",
            "city": "Colombo",
            "district": "Colombo",
            "province": "Western",
            "managed_by": "sales.user@example.com",
        }
    )
    output.seek(0)

    return Response(
        output.read(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=dealers_bulk_template.csv",
            "Cache-Control": "no-store",
        },
    )


@bp.post("/bulk-validate")
@jwt_required()
def bulk_validate():
    guard = _guard_bulk_roles()
    if guard:
        return guard

    company, err = _load_company()
    if err:
        return err

    rows, read_errors = _read_csv(request.files.get("file"))
    if read_errors:
        return jsonify({"ok": False, "errors": read_errors}), 400

    valid, failed = _validate_rows(company, rows)
    preview_rows = [
        {
            **{key: row.data.get(key) for key in TEMPLATE_COLUMNS},
            "row_number": row.row_number,
            "error": row.error,
            "managed_by_user_id": row.managed_by_user_id,
        }
        for row in rows[:10]
    ]

    return jsonify(
        {
            "ok": True,
            "data": {
                "rows": preview_rows,
                "valid_count": len(valid),
                "failed_count": len(failed),
            },
        }
    )


@bp.post("/bulk-import")
@jwt_required()
def bulk_import():
    guard = _guard_bulk_roles()
    if guard:
        return guard

    current_user = _current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    company, err = _load_company()
    if err:
        return err

    strict_mode = (request.form.get("strict_mode") or "").lower() in {"1", "true", "yes", "on"}

    rows, read_errors = _read_csv(request.files.get("file"))
    if read_errors:
        return jsonify({"ok": False, "errors": read_errors}), 400

    valid_rows, failed_rows = _validate_rows(company, rows)

    if strict_mode and failed_rows:
        token, _ = _generate_error_report(failed_rows)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Validation failed",
                    "failed_count": len(failed_rows),
                    "failed_rows": _serialize_rows(failed_rows),
                    "error_report_token": token,
                }
            ),
            400,
        )

    inserted_count = 0
    generated_codes_count = 0
    assigned_codes: set[str] = set()

    try:
        for row in valid_rows:
            customer_code = row.provided_code
            if not customer_code:
                customer_code = nsc._generate_customer_code_for_company(company, lock=True)
                generated_codes_count += 1

            if customer_code in assigned_codes:
                row.error = "Duplicate customer_code within import batch"
                failed_rows.append(row)
                if strict_mode:
                    raise ValueError("Duplicate code in batch")
                continue

            assigned_codes.add(customer_code)

            dealer = NonSamproxCustomer(
                customer_code=customer_code,
                customer_name=row.data.get("customer_name"),
                area_code=row.data.get("area_code"),
                city=row.data.get("city"),
                district=row.data.get("district"),
                province=row.data.get("province"),
                managed_by_user_id=row.managed_by_user_id,
                company_id=company.id,
                managed_by_label=row.managed_by_name,
                company_label=company.name,
                created_by=current_user.id,
                source="bulk_import",
            )
            db.session.add(dealer)
            inserted_count += 1

        if strict_mode and failed_rows:
            raise ValueError("Validation failed in strict mode")

        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return (
            jsonify({"ok": False, "error": "Unable to import dealers", "details": str(exc.orig)}),
            400,
        )
    except Exception as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc) or "Import failed"}), 400

    token, _ = _generate_error_report(failed_rows)
    serialized_failed = _serialize_rows(failed_rows)

    return jsonify(
        {
            "ok": True,
            "data": {
                "inserted_count": inserted_count,
                "failed_count": len(failed_rows),
                "generated_codes_count": generated_codes_count,
                "failed_rows": serialized_failed,
                "error_report_token": token,
            },
        }
    )


@bp.get("/bulk-error-report/<token>")
@jwt_required()
def bulk_error_report(token: str):
    guard = _guard_bulk_roles()
    if guard:
        return guard

    safe_token = token.replace("/", "").replace("\\", "")
    path = Path(current_app.instance_path or ".") / "dealer_error_reports" / f"{safe_token}.csv"

    if not path.exists():
        return jsonify({"ok": False, "error": "Error report not found"}), 404

    return send_file(path, mimetype="text/csv", as_attachment=True, download_name="dealer_errors.csv")
