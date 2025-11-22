from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import uuid

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from company_profiles import resolve_company_profile, select_company_key
from extensions import db
from models import (
    PettyCashStatus,
    PettyCashWeeklyClaim,
    PettyCashWeeklyLine,
    RoleEnum,
    User,
)

bp = Blueprint("petty_cash", __name__, url_prefix="/api/petty-cash")

DEFAULT_EXPENSE_TYPES = [
    "Fuel",
    "Vehicle Maintenance",
    "Breakfast",
    "Lunch",
    "Dinner",
    "Lodging",
    "Parking Fees",
    "Transport Charges",
    "Communications",
    "Miscellaneous",
    "Stationery",
    "High Way Charges",
]


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


def _current_user() -> User | None:
    identity = get_jwt_identity()
    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return None
    return User.query.get(user_id)


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
        "status": claim.status.value,
        "total_expenses": float(claim.total_expenses or 0),
        "lines": [_serialize_line(line) for line in claim.lines],
    }


def _require_claim_owner(claim: PettyCashWeeklyClaim, current_user: User | None) -> None:
    if current_user is None:
        raise PettyCashError("Not authenticated", 401)
    if claim.created_by_id != current_user.id and current_user.role not in {
        RoleEnum.admin,
        RoleEnum.finance_manager,
    }:
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


def _claim_is_locked(claim: PettyCashWeeklyClaim) -> bool:
    return claim.status in {PettyCashStatus.approved, PettyCashStatus.paid}


@bp.get("/company")
@jwt_required()
def company_profile():
    claims = get_jwt() or {}
    selected = select_company_key(current_app.config, None, claims)
    profile = resolve_company_profile(current_app.config, selected)
    return jsonify({"key": profile.get("key"), "name": profile.get("name")})


@bp.get("/employees")
@jwt_required()
def list_employees():
    claims = get_jwt() or {}
    company_key = claims.get("company_key")
    query = User.query.filter_by(active=True)
    if company_key:
        query = query.filter(User.company_key == company_key)
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
        return jsonify({"claim": _serialize_claim(existing)})

    sheet_no = _generate_sheet_number(week_start)
    claim = PettyCashWeeklyClaim(
        employee_id=current.id,
        employee_name=current.name,
        company_id=company_key,
        sheet_no=sheet_no,
        week_start_date=week_start,
        week_end_date=week_end,
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


@bp.get("/weekly-claims/<int:claim_id>")
@jwt_required()
def get_claim(claim_id: int):
    claim = PettyCashWeeklyClaim.query.get(claim_id)
    if not claim:
        return jsonify({"msg": "Claim not found"}), 404
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

    if _claim_is_locked(claim):
        return jsonify({"msg": "Approved or paid claims cannot be edited."}), 400

    payload = request.get_json(silent=True) or {}
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

    is_manager = user.role in {RoleEnum.admin, RoleEnum.finance_manager, RoleEnum.production_manager}
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

    if _claim_is_locked(claim):
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

    if _claim_is_locked(claim):
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

    if _claim_is_locked(claim):
        return jsonify({"msg": "Approved or paid claims cannot be edited."}), 400

    db.session.delete(line)
    _resequence_lines(claim)
    claim.recalculate_totals()
    db.session.commit()
    db.session.refresh(claim)
    return jsonify({"claim": _serialize_claim(claim)})
