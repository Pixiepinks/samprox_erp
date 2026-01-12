from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import uuid

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from company_profiles import available_company_keys, resolve_company_profile, select_company_key
from extensions import db
from models import (
    PettyCashStatus,
    PettyCashWeeklyClaim,
    PettyCashWeeklyLine,
    RoleEnum,
    User,
)

bp = Blueprint("petty_cash", __name__, url_prefix="/api/petty-cash")

# Weekly travel claims are intentionally scoped to a small set of roles. Sales
# users can access only this module while broader finance roles retain their
# existing permissions.
_WEEKLY_CLAIM_ROLES = {
    RoleEnum.admin,
    RoleEnum.finance_manager,
    RoleEnum.production_manager,
    RoleEnum.maintenance_manager,
    RoleEnum.outside_manager,
    RoleEnum.sales,
}

DEFAULT_EXPENSE_TYPES = [
    "Breakfast",
    "Lunch",
    "Dinner",
    "Lodging",
    "High Way",
]

DISALLOWED_DEFAULT_EXPENSE_TYPES = {
    "fuel",
    "vehicle maintenance",
    "stationery",
}


class PettyCashError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _decimal_or_zero(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if text == "":
        return Decimal("0")
    try:
        return Decimal(text)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise PettyCashError("Amounts must be numeric.") from exc


def _extract_user_id(identity: object) -> int | None:
    """Try to resolve a user id from flexible JWT identity formats."""

    if isinstance(identity, dict):
        # Tokens may store the user id directly on the identity payload, inside a
        # "sub" mapping (either as an object or a scalar), or with alternate keys
        # such as "user_id". Handle all those shapes defensively so UI calls do
        # not fail silently.
        for key in ("id", "user_id"):
            if identity.get(key) is not None:
                try:
                    return int(identity.get(key))
                except (TypeError, ValueError):
                    return None
        sub_identity = identity.get("sub")
        if isinstance(sub_identity, dict):
            for key in ("id", "user_id"):
                nested_value = sub_identity.get(key)
                if nested_value is not None:
                    try:
                        return int(nested_value)
                    except (TypeError, ValueError):
                        return None
        elif sub_identity is not None:
            try:
                return int(sub_identity)
            except (TypeError, ValueError):
                return None
    else:
        try:
            return int(identity)
        except (TypeError, ValueError):
            return None

    return None


def _current_user() -> User | None:
    identity = get_jwt_identity()
    user_id = _extract_user_id(identity)
    if user_id is None:
        return None

    return User.query.get(user_id)


def _current_role() -> RoleEnum | None:
    claims = get_jwt() or {}
    try:
        return RoleEnum(claims.get("role"))
    except Exception:
        return None


def _current_employee_id(user: User | None) -> int | None:
    if user is None:
        return None
    if getattr(user, "employee_id", None):
        return user.employee_id
    return user.id


@bp.before_request
@jwt_required()
def _enforce_weekly_claim_roles():
    role = _current_role()
    if role not in _WEEKLY_CLAIM_ROLES:
        return jsonify({"msg": "Access denied"}), 403


def _serialize_line(line: PettyCashWeeklyLine) -> dict[str, object]:
    return {
        "id": line.id,
        "line_order": line.line_order,
        "expense_type": line.expense_type or "",
        "mon_amount": float(line.mon_amount or 0),
        "tue_amount": float(line.tue_amount or 0),
        "wed_amount": float(line.wed_amount or 0),
        "thu_amount": float(line.thu_amount or 0),
        "fri_amount": float(line.fri_amount or 0),
        "sat_amount": float(line.sat_amount or 0),
        "sun_amount": float(line.sun_amount or 0),
        "row_total": float(line.row_total or 0),
    }


def _serialize_claim(claim: PettyCashWeeklyClaim) -> dict[str, object]:
    profile = resolve_company_profile(current_app.config, claim.company_id)
    return {
        "id": claim.id,
        "company": {
            "key": profile.get("key"),
            "name": profile.get("name") or claim.company_id,
        },
        "employee": {
            "id": claim.employee_id,
            "name": claim.employee_name,
        },
        "sheet_no": claim.sheet_no,
        "week_start_date": claim.week_start_date.isoformat(),
        "week_end_date": claim.week_end_date.isoformat(),
        "vehicle_no": claim.vehicle_no or "",
        "area_visited": claim.area_visited or "",
        "monday_morning_odo": float(claim.monday_morning_odo)
        if claim.monday_morning_odo is not None
        else None,
        "friday_evening_odo": float(claim.friday_evening_odo)
        if claim.friday_evening_odo is not None
        else None,
        "status": claim.status.value,
        "total_expenses": float(claim.total_expenses or 0),
        "lines": [_serialize_line(line) for line in claim.lines],
    }


def _has_full_petty_cash_access(user: User | None) -> bool:
    return user is not None and user.role in {
        RoleEnum.admin,
        RoleEnum.finance_manager,
        RoleEnum.production_manager,
        RoleEnum.maintenance_manager,
        RoleEnum.outside_manager,
    }


def _require_claim_owner(claim: PettyCashWeeklyClaim, current_user: User | None) -> None:
    if current_user is None:
        raise PettyCashError("Not authenticated", 401)
    if (
        claim.created_by_id != current_user.id
        and claim.employee_id != current_user.id
        and not _has_full_petty_cash_access(current_user)
    ):
        raise PettyCashError("You are not allowed to modify this claim", 403)


def _parse_week_start(raw_value: str | None) -> date:
    if not raw_value:
        today = date.today()
        return today - timedelta(days=today.weekday())
    try:
        parsed = date.fromisoformat(raw_value)
    except ValueError:
        raise PettyCashError("Invalid week_start_date. Use YYYY-MM-DD.")
    if parsed.weekday() != 0:
        raise PettyCashError("Week starting date must be a Monday.")
    return parsed


def _generate_sheet_number(week_start: date) -> str:
    suffix = uuid.uuid4().hex[:6].upper()
    return f"PCW-{week_start.strftime('%Y%m%d')}-{suffix}"


def _apply_amount_updates(line: PettyCashWeeklyLine, payload: dict[str, object]) -> None:
    for field in [
        "mon_amount",
        "tue_amount",
        "wed_amount",
        "thu_amount",
        "fri_amount",
        "sat_amount",
        "sun_amount",
    ]:
        if field in payload:
            value = payload.get(field)
            if isinstance(value, str) and not value.strip():
                value = Decimal("0")
            amount = _decimal_or_zero(value)
            line.__setattr__(field, amount)
    if "expense_type" in payload:
        line.expense_type = (payload.get("expense_type") or "").strip()
    line.recalculate_total()


def _resequence_lines(claim: PettyCashWeeklyClaim) -> None:
    for index, line in enumerate(sorted(claim.lines, key=lambda l: l.line_order), start=1):
        line.line_order = index


def _prune_disallowed_lines(claim: PettyCashWeeklyClaim) -> bool:
    removed = False
    for line in list(claim.lines):
        expense_name = (line.expense_type or "").strip().lower()
        if expense_name in DISALLOWED_DEFAULT_EXPENSE_TYPES:
            db.session.delete(line)
            removed = True

    if removed:
        _resequence_lines(claim)
        claim.recalculate_totals()
        db.session.commit()
        db.session.refresh(claim)

    return removed


def _ensure_default_lines(claim: PettyCashWeeklyClaim) -> bool:
    existing = {(line.expense_type or "").strip().lower(): line for line in claim.lines}
    existing_orders = [line.line_order or 0 for line in claim.lines]
    next_order = (max(existing_orders) if existing_orders else 0) + 1
    added = False

    for expense in DEFAULT_EXPENSE_TYPES:
        normalized = expense.strip().lower()
        if normalized in existing:
            continue
        line = PettyCashWeeklyLine(
            claim_id=claim.id,
            line_order=next_order,
            expense_type=expense,
        )
        line.recalculate_total()
        db.session.add(line)
        next_order += 1
        added = True

    if added:
        _resequence_lines(claim)
        claim.recalculate_totals()
        db.session.commit()
        db.session.refresh(claim)

    return added


def _claim_is_locked(claim: PettyCashWeeklyClaim, user: User | None) -> bool:
    if _has_full_petty_cash_access(user):
        return False
    return claim.status in {PettyCashStatus.approved, PettyCashStatus.paid}


@bp.get("/company")
@jwt_required()
def company_profile():
    claims = get_jwt() or {}
    selected = select_company_key(current_app.config, None, claims)
    profile = resolve_company_profile(current_app.config, selected)
    return jsonify({"key": profile.get("key"), "name": profile.get("name")})


@bp.get("/companies")
@jwt_required()
def list_companies():
    claims = get_jwt() or {}
    selected = select_company_key(current_app.config, None, claims)

    options = []
    for key in available_company_keys(current_app.config):
        profile = resolve_company_profile(current_app.config, key)
        options.append({"key": profile.get("key"), "name": profile.get("name")})

    if selected and not any(option.get("key") == selected for option in options):
        profile = resolve_company_profile(current_app.config, selected)
        options.insert(0, {"key": profile.get("key"), "name": profile.get("name")})

    return jsonify(options)


@bp.get("/employees")
@jwt_required()
def list_employees():
    claims = get_jwt() or {}
    requested_key = request.args.get("company")
    company_key = select_company_key(current_app.config, requested_key, claims)
    user = _current_user()
    if user is None:
        return jsonify({"msg": "Not authenticated"}), 401

    allowed_company_keys = set(available_company_keys(current_app.config))
    if (not company_key or company_key not in allowed_company_keys) and user.company_key:
        if user.company_key in allowed_company_keys:
            company_key = user.company_key

    query = User.query.filter_by(active=True)
    if company_key:
        query = query.filter(User.company_key == company_key)

    if user.role == RoleEnum.sales:
        employee_id = _current_employee_id(user)
        query = query.filter(User.id == employee_id)

    employees = query.order_by(User.name.asc()).all()
    return jsonify(
        [
            {"id": employee.id, "name": employee.name}
            for employee in employees
        ]
    )


@bp.get("/weekly-claims/init")
@jwt_required()
def init_claim():
    current = _current_user()
    if current is None:
        return jsonify({"msg": "User not found"}), 404

    claims = get_jwt() or {}
    company_key = select_company_key(current_app.config, None, claims)
    allowed_company_keys = set(available_company_keys(current_app.config))
    if (not company_key or company_key not in allowed_company_keys) and current.company_key:
        if current.company_key in allowed_company_keys:
            company_key = current.company_key

    week_start = _parse_week_start(request.args.get("week_start"))
    week_end = week_start + timedelta(days=6)

    existing = (
        PettyCashWeeklyClaim.query.filter_by(
            employee_id=current.id,
            week_start_date=week_start,
            company_id=company_key,
        )
        .order_by(PettyCashWeeklyClaim.id.desc())
        .first()
    )
    if existing:
        _prune_disallowed_lines(existing)
        _ensure_default_lines(existing)
        return jsonify({"claim": _serialize_claim(existing)})

    sheet_no = _generate_sheet_number(week_start)
    claim = PettyCashWeeklyClaim(
        employee_id=current.id,
        employee_name=current.name,
        company_id=company_key,
        sheet_no=sheet_no,
        week_start_date=week_start,
        week_end_date=week_end,
        # Use the enum member itself so SQLAlchemy writes the canonical title-
        # case value (e.g. "Draft") expected by the PostgreSQL enum instead of
        # a lowercase string that would be rejected.
        status=PettyCashStatus.draft,
        created_by_id=current.id,
    )
    db.session.add(claim)
    db.session.flush()

    for order, expense in enumerate(DEFAULT_EXPENSE_TYPES, start=1):
        line = PettyCashWeeklyLine(
            claim_id=claim.id,
            line_order=order,
            expense_type=expense,
        )
        line.recalculate_total()
        db.session.add(line)

    claim.recalculate_totals()
    db.session.commit()
    db.session.refresh(claim)
    return jsonify({"claim": _serialize_claim(claim)}), 201


@bp.get("/weekly-claims")
@jwt_required()
def list_claims():
    user = _current_user()
    if user is None:
        return jsonify({"msg": "Not authenticated"}), 401

    manager_roles = {
        RoleEnum.admin,
        RoleEnum.finance_manager,
        RoleEnum.production_manager,
        RoleEnum.maintenance_manager,
        RoleEnum.sales_manager,
        RoleEnum.sales_executive,
    }
    if user.role not in manager_roles and user.role != RoleEnum.sales:
        return jsonify({"msg": "You are not allowed to view weekly claims."}), 403

    try:
        week_start = _parse_week_start(request.args.get("week_start"))
    except PettyCashError as exc:
        return jsonify({"msg": exc.message}), exc.status_code

    status_filter = (request.args.get("status") or "ALL").upper()
    employee_raw = request.args.get("employee_id")
    employee_id = request.args.get("employee_id", type=int)
    if employee_raw is not None and employee_raw != "" and employee_id is None:
        return jsonify({"msg": "employee_id must be an integer"}), 400

    if user.role == RoleEnum.sales:
        employee_id = _current_employee_id(user)

    query = PettyCashWeeklyClaim.query.filter(
        PettyCashWeeklyClaim.week_start_date == week_start
    )

    if status_filter != "ALL":
        try:
            status_enum = PettyCashStatus(status_filter.title())
        except ValueError:
            return jsonify({"msg": "Invalid status filter"}), 400
        query = query.filter(PettyCashWeeklyClaim.status == status_enum)

    if employee_id:
        query = query.filter(PettyCashWeeklyClaim.employee_id == employee_id)

    claims = (
        query.order_by(PettyCashWeeklyClaim.updated_at.desc())
        .with_entities(
            PettyCashWeeklyClaim.id,
            PettyCashWeeklyClaim.sheet_no,
            PettyCashWeeklyClaim.employee_id,
            PettyCashWeeklyClaim.employee_name,
            PettyCashWeeklyClaim.week_start_date,
            PettyCashWeeklyClaim.status,
            PettyCashWeeklyClaim.total_expenses,
            PettyCashWeeklyClaim.created_at,
            PettyCashWeeklyClaim.updated_at,
            PettyCashWeeklyClaim.monday_morning_odo,
            PettyCashWeeklyClaim.friday_evening_odo,
        )
        .all()
    )

    results = []
    for claim in claims:
        results.append(
            {
                "id": claim.id,
                "sheet_no": claim.sheet_no,
                "employee_id": claim.employee_id,
                "employee_name": claim.employee_name,
                "week_start": claim.week_start_date.isoformat(),
                "status": claim.status.value,
                "total_amount": float(claim.total_expenses or 0),
                "submitted_at": claim.created_at.isoformat() if claim.created_at else None,
                "updated_at": claim.updated_at.isoformat() if claim.updated_at else None,
                "monday_morning_odo": float(claim.monday_morning_odo)
                if claim.monday_morning_odo is not None
                else None,
                "friday_evening_odo": float(claim.friday_evening_odo)
                if claim.friday_evening_odo is not None
                else None,
            }
        )

    return jsonify({"claims": results})


@bp.get("/weekly-claims/<int:claim_id>")
@jwt_required()
def get_claim(claim_id: int):
    claim = PettyCashWeeklyClaim.query.get(claim_id)
    if not claim:
        return jsonify({"msg": "Claim not found"}), 404

    user = _current_user()
    if user is None:
        return jsonify({"msg": "Not authenticated"}), 401

    is_owner = claim.employee_id == user.id or claim.created_by_id == user.id
    is_manager = user.role in {
        RoleEnum.admin,
        RoleEnum.finance_manager,
        RoleEnum.production_manager,
        RoleEnum.sales_manager,
        RoleEnum.sales_executive,
    }

    if not is_owner and not is_manager:
        return jsonify({"msg": "You are not allowed to view this claim."}), 403

    _prune_disallowed_lines(claim)
    _ensure_default_lines(claim)
    return jsonify({"claim": _serialize_claim(claim)})


@bp.put("/weekly-claims/<int:claim_id>")
@jwt_required()
def update_claim(claim_id: int):
    claim = PettyCashWeeklyClaim.query.get(claim_id)
    if not claim:
        return jsonify({"msg": "Claim not found"}), 404

    user = _current_user()
    try:
        _require_claim_owner(claim, user)
    except PettyCashError as exc:
        return jsonify({"msg": exc.message}), exc.status_code

    if _claim_is_locked(claim, user):
        return jsonify({"msg": "Approved or paid claims cannot be edited."}), 400

    payload = request.get_json(silent=True) or {}
    if user.role == RoleEnum.sales:
        payload["employee_id"] = _current_employee_id(user)

    if "employee_id" in payload:
        employee_id = payload.get("employee_id")
        employee = User.query.get(employee_id) if employee_id else None
        if not employee:
            return jsonify({"msg": "Employee not found"}), 400
        claim.employee_id = employee.id
        claim.employee_name = employee.name

    if "week_start_date" in payload:
        try:
            week_start = _parse_week_start(payload.get("week_start_date"))
        except PettyCashError as exc:
            return jsonify({"msg": exc.message}), exc.status_code
        claim.week_start_date = week_start
        claim.week_end_date = week_start + timedelta(days=6)

    if "vehicle_no" in payload:
        claim.vehicle_no = (payload.get("vehicle_no") or "").strip()

    if "area_visited" in payload:
        claim.area_visited = (payload.get("area_visited") or "").strip()

    try:
        def _optional_decimal(raw_value: object, label: str) -> Decimal | None:
            if raw_value is None or raw_value == "":
                return None
            try:
                return Decimal(str(raw_value))
            except Exception as exc:  # pragma: no cover - defensive guard
                raise PettyCashError(f"{label} must be a number.") from exc

        monday_value = claim.monday_morning_odo
        friday_value = claim.friday_evening_odo

        if "monday_morning_odo" in payload:
            monday_value = _optional_decimal(
                payload.get("monday_morning_odo"), "Monday morning ODO"
            )

        if "friday_evening_odo" in payload:
            friday_value = _optional_decimal(
                payload.get("friday_evening_odo"), "Friday evening ODO"
            )

        if (
            monday_value is not None
            and friday_value is not None
            and friday_value < monday_value
        ):
            return (
                jsonify({
                    "msg": "Friday evening ODO must be greater than or equal to Monday morning ODO.",
                }),
                400,
            )

        if "monday_morning_odo" in payload:
            claim.monday_morning_odo = monday_value

        if "friday_evening_odo" in payload:
            claim.friday_evening_odo = friday_value
    except PettyCashError as exc:
        return jsonify({"msg": exc.message}), exc.status_code

    if "company_id" in payload:
        claims = get_jwt() or {}
        company_key = select_company_key(current_app.config, payload.get("company_id"), claims)
        claim.company_id = company_key

    db.session.commit()
    db.session.refresh(claim)
    return jsonify({"claim": _serialize_claim(claim)})


@bp.post("/weekly-claims/<int:claim_id>/status")
@jwt_required()
def update_status(claim_id: int):
    claim = PettyCashWeeklyClaim.query.get(claim_id)
    if not claim:
        return jsonify({"msg": "Claim not found"}), 404

    user = _current_user()
    if user is None:
        return jsonify({"msg": "Not authenticated"}), 401

    payload = request.get_json(silent=True) or {}
    requested_status = payload.get("status")
    try:
        new_status = PettyCashStatus(requested_status)
    except ValueError:
        return jsonify({"msg": "Invalid status"}), 400

    is_manager = user.role in {
        RoleEnum.admin,
        RoleEnum.finance_manager,
        RoleEnum.production_manager,
        RoleEnum.sales_manager,
        RoleEnum.sales_executive,
    }
    is_owner = claim.created_by_id == user.id
    if not is_owner and not is_manager:
        return jsonify({"msg": "You are not allowed to update this claim."}), 403

    if new_status in {PettyCashStatus.approved, PettyCashStatus.paid, PettyCashStatus.rejected} and not is_manager:
        return jsonify({"msg": "Only managers or finance can perform this action."}), 403

    claim.status = new_status
    db.session.commit()
    db.session.refresh(claim)
    return jsonify({"claim": _serialize_claim(claim)})


@bp.post("/weekly-claims/<int:claim_id>/lines")
@jwt_required()
def add_line(claim_id: int):
    claim = PettyCashWeeklyClaim.query.get(claim_id)
    if not claim:
        return jsonify({"msg": "Claim not found"}), 404

    user = _current_user()
    try:
        _require_claim_owner(claim, user)
    except PettyCashError as exc:
        return jsonify({"msg": exc.message}), exc.status_code

    if _claim_is_locked(claim, user):
        return jsonify({"msg": "Approved or paid claims cannot be edited."}), 400

    next_order = (max([line.line_order for line in claim.lines]) if claim.lines else 0) + 1
    payload = request.get_json(silent=True) or {}
    expense_type = (payload.get("expense_type") or "").strip()
    line = PettyCashWeeklyLine(
        claim_id=claim.id,
        line_order=next_order,
        expense_type=expense_type,
    )
    line.recalculate_total()
    db.session.add(line)
    claim.recalculate_totals()
    db.session.commit()
    db.session.refresh(claim)
    return jsonify({"claim": _serialize_claim(claim)}), 201


@bp.put("/weekly-claims/<int:claim_id>/lines/<int:line_id>")
@jwt_required()
def update_line(claim_id: int, line_id: int):
    claim = PettyCashWeeklyClaim.query.get(claim_id)
    if not claim:
        return jsonify({"msg": "Claim not found"}), 404

    line = PettyCashWeeklyLine.query.get(line_id)
    if not line or line.claim_id != claim.id:
        return jsonify({"msg": "Line not found"}), 404

    user = _current_user()
    try:
        _require_claim_owner(claim, user)
    except PettyCashError as exc:
        return jsonify({"msg": exc.message}), exc.status_code

    if _claim_is_locked(claim, user):
        return jsonify({"msg": "Approved or paid claims cannot be edited."}), 400

    payload = request.get_json(silent=True) or {}
    try:
        _apply_amount_updates(line, payload)
    except PettyCashError as exc:
        return jsonify({"msg": exc.message}), exc.status_code
    claim.recalculate_totals()
    db.session.commit()
    db.session.refresh(claim)
    return jsonify({"claim": _serialize_claim(claim)})


@bp.delete("/weekly-claims/<int:claim_id>/lines/<int:line_id>")
@jwt_required()
def delete_line(claim_id: int, line_id: int):
    claim = PettyCashWeeklyClaim.query.get(claim_id)
    if not claim:
        return jsonify({"msg": "Claim not found"}), 404

    line = PettyCashWeeklyLine.query.get(line_id)
    if not line or line.claim_id != claim.id:
        return jsonify({"msg": "Line not found"}), 404

    user = _current_user()
    try:
        _require_claim_owner(claim, user)
    except PettyCashError as exc:
        return jsonify({"msg": exc.message}), exc.status_code

    if _claim_is_locked(claim, user):
        return jsonify({"msg": "Approved or paid claims cannot be edited."}), 400

    db.session.delete(line)
    _resequence_lines(claim)
    claim.recalculate_totals()
    db.session.commit()
    db.session.refresh(claim)
    return jsonify({"claim": _serialize_claim(claim)})
