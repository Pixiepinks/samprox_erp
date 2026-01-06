from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
import uuid
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Customer,
    NonSamproxCustomer,
    SalesTeamMember,
    SalesVisit,
    SalesVisitApprovalStatus,
    SalesVisitAttachment,
    User,
    RoleEnum,
    SALES_MANAGER_ROLES,
    haversine_distance_meters,
)

bp = Blueprint("sales_visits", __name__, url_prefix="/api/sales-visits")

COLOMBO_TZ = ZoneInfo("Asia/Colombo")


def _extract_user_id(identity: object) -> Optional[int]:
    if isinstance(identity, dict):
        for key in ("id", "user_id", "sub"):
            if identity.get(key) is not None:
                try:
                    return int(identity.get(key))
                except (TypeError, ValueError):
                    return None
    try:
        return int(identity)
    except (TypeError, ValueError):
        return None


def _current_user() -> Optional[User]:
    identity = get_jwt_identity()
    user_id = _extract_user_id(identity)
    if user_id is None:
        return None
    return User.query.get(user_id)


def _current_role() -> Optional[RoleEnum]:
    claims = get_jwt() or {}
    try:
        return RoleEnum(claims.get("role"))
    except Exception:
        return None


def _serialize_attachment(att: SalesVisitAttachment) -> dict[str, Any]:
    return {
        "id": str(att.id),
        "visit_id": str(att.visit_id),
        "file_url": att.file_url,
        "file_type": att.file_type or "",
        "uploaded_by": att.uploaded_by,
        "uploaded_at": att.uploaded_at.isoformat() if att.uploaded_at else None,
    }


def _serialize_visit(visit: SalesVisit) -> dict[str, Any]:
    non_samprox_customer = visit.non_samprox_customer
    legacy_customer = visit.customer
    display_name = (
        getattr(non_samprox_customer, "customer_name", None)
        or getattr(legacy_customer, "name", None)
        or visit.prospect_name
        or "-"
    )
    return {
        "id": str(visit.id),
        "visit_no": visit.visit_no,
        "sales_user_id": visit.sales_user_id,
        "sales_user_name": getattr(visit.user, "name", None),
        "customer_id": visit.customer_id,
        "customer_name": getattr(legacy_customer, "name", None),
        "non_samprox_customer_id": str(visit.non_samprox_customer_id) if visit.non_samprox_customer_id else None,
        "non_samprox_customer_name": getattr(non_samprox_customer, "customer_name", None),
        "non_samprox_customer_code": getattr(non_samprox_customer, "customer_code", None),
        "non_samprox_customer_city": getattr(non_samprox_customer, "city", None),
        "customer_display_name": display_name,
        "prospect_name": visit.prospect_name,
        "visit_date": visit.visit_date.isoformat() if visit.visit_date else None,
        "planned": bool(visit.planned),
        "purpose": visit.purpose,
        "remarks": visit.remarks,
        "check_in_time": visit.check_in_time.isoformat() if visit.check_in_time else None,
        "check_out_time": visit.check_out_time.isoformat() if visit.check_out_time else None,
        "check_in_lat": float(visit.check_in_lat) if visit.check_in_lat is not None else None,
        "check_in_lng": float(visit.check_in_lng) if visit.check_in_lng is not None else None,
        "check_in_accuracy_m": visit.check_in_accuracy_m,
        "check_out_lat": float(visit.check_out_lat) if visit.check_out_lat is not None else None,
        "check_out_lng": float(visit.check_out_lng) if visit.check_out_lng is not None else None,
        "check_out_accuracy_m": visit.check_out_accuracy_m,
        "distance_from_customer_m": visit.distance_from_customer_m,
        "duration_minutes": visit.duration_minutes,
        "gps_mismatch": bool(visit.gps_mismatch),
        "short_duration": bool(visit.short_duration),
        "manual_location_override": bool(visit.manual_location_override),
        "exception_reason": visit.exception_reason,
        "approval_status": visit.approval_status.value if visit.approval_status else None,
        "approved_by": visit.approved_by,
        "approved_by_name": getattr(visit.approver, "name", None),
        "approved_at": visit.approved_at.isoformat() if visit.approved_at else None,
        "approval_note": visit.approval_note,
        "attachments": [_serialize_attachment(att) for att in visit.attachments],
    }


def _is_admin(role: Optional[RoleEnum]) -> bool:
    return role in {RoleEnum.admin, *SALES_MANAGER_ROLES}


def _can_view_sales_overview(role: Optional[RoleEnum]) -> bool:
    return role in {
        RoleEnum.admin,
        RoleEnum.finance_manager,
        RoleEnum.production_manager,
        *SALES_MANAGER_ROLES,
    }


def _is_owner(visit: SalesVisit, user: Optional[User]) -> bool:
    return user is not None and visit.sales_user_id == user.id


def _manager_sales_ids(manager_id: int) -> set[int]:
    rows = SalesTeamMember.query.filter_by(manager_user_id=manager_id).all()
    return {row.sales_user_id for row in rows}


def _visit_requires_approval(visit: SalesVisit) -> bool:
    return visit.gps_mismatch or visit.short_duration or visit.manual_location_override


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=COLOMBO_TZ)
        return dt
    except ValueError:
        return None


@bp.before_request
@jwt_required()
def _guard_roles():
    role = _current_role()
    if role not in {
        RoleEnum.sales,
        RoleEnum.outside_manager,
        RoleEnum.admin,
        *SALES_MANAGER_ROLES,
        RoleEnum.finance_manager,
        RoleEnum.production_manager,
    }:
        return jsonify({"ok": False, "error": "Access denied"}), 403


def _load_non_samprox_customer(customer_id: object, role: RoleEnum, user: User) -> tuple[Optional[NonSamproxCustomer], Optional[tuple]]:
    try:
        parsed_id = str(uuid.UUID(str(customer_id)))
    except (TypeError, ValueError):
        return None, (jsonify({"ok": False, "error": "Invalid non_samprox_customer_id"}), 400)

    customer = NonSamproxCustomer.query.get(parsed_id)
    if not customer:
        return None, (jsonify({"ok": False, "error": "Non Samprox customer not found"}), 404)

    if role == RoleEnum.sales and customer.managed_by_user_id != user.id:
        return None, (jsonify({"ok": False, "error": "Not authorized for this customer"}), 403)
    if role == RoleEnum.outside_manager and customer.managed_by_user_id not in _manager_sales_ids(user.id) | {user.id}:
        return None, (jsonify({"ok": False, "error": "Not authorized for this customer"}), 403)

    return customer, None


@bp.get("")
def list_visits():
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"))
    sales_user_id_param = request.args.get("sales_user_id")

    query = SalesVisit.query
    team_ids: set[int] = set()

    if role == RoleEnum.sales:
        query = query.filter(SalesVisit.sales_user_id == user.id)
    elif role == RoleEnum.outside_manager:
        team_ids = _manager_sales_ids(user.id)
        query = query.filter(SalesVisit.sales_user_id.in_(team_ids or {-1}))
    elif not _can_view_sales_overview(role):
        return jsonify({"ok": False, "error": "Access denied"}), 403

    if sales_user_id_param and role in {
        RoleEnum.outside_manager,
        RoleEnum.admin,
        *SALES_MANAGER_ROLES,
        RoleEnum.finance_manager,
        RoleEnum.production_manager,
    }:
        try:
            target_id = int(sales_user_id_param)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid sales_user_id"}), 400

        if role == RoleEnum.outside_manager and target_id not in team_ids:
            return jsonify({"ok": False, "error": "Not authorized for this sales user"}), 403
        query = query.filter(SalesVisit.sales_user_id == target_id)

    if date_from:
        query = query.filter(SalesVisit.visit_date >= date_from)
    if date_to:
        query = query.filter(SalesVisit.visit_date <= date_to)

    visits = query.order_by(SalesVisit.visit_date.desc(), SalesVisit.visit_no.desc()).all()
    return jsonify({"ok": True, "data": [_serialize_visit(v) for v in visits]})


@bp.post("")
def create_visit():
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    if role not in {RoleEnum.sales, RoleEnum.outside_manager, RoleEnum.admin, *SALES_MANAGER_ROLES}:
        return jsonify({"ok": False, "error": "Not authorized to create visits"}), 403

    payload = request.get_json() or {}
    customer_id = payload.get("customer_id")
    prospect_name = (payload.get("prospect_name") or "").strip() or None
    non_samprox_customer_id = payload.get("non_samprox_customer_id")

    target_user_id = user.id
    if payload.get("sales_user_id"):
        try:
            requested_user_id = int(payload["sales_user_id"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid sales_user_id"}), 400
        if _is_admin(role):
            target_user_id = requested_user_id
        else:
            return jsonify({"ok": False, "error": "Cannot create visits for other users"}), 403

    customer = None
    if customer_id:
        try:
            customer_id = int(customer_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid customer_id"}), 400
        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({"ok": False, "error": "Customer not found"}), 404

    non_samprox_customer = None
    if non_samprox_customer_id:
        non_samprox_customer, error = _load_non_samprox_customer(non_samprox_customer_id, role, user)
        if error:
            return error

    visit_date_value = _parse_date(payload.get("visit_date")) or datetime.now(tz=COLOMBO_TZ).date()

    visit = SalesVisit(
        visit_no=SalesVisit.generate_visit_no(visit_date_value),
        sales_user_id=target_user_id,
        customer_id=customer.id if customer else None,
        non_samprox_customer_id=non_samprox_customer.id if non_samprox_customer else None,
        prospect_name=prospect_name,
        visit_date=visit_date_value,
        planned=bool(payload.get("planned")),
        purpose=payload.get("purpose"),
        remarks=payload.get("remarks"),
        created_by=user.id,
        updated_by=user.id,
    )

    db.session.add(visit)
    db.session.commit()
    return jsonify({"ok": True, "data": _serialize_visit(visit)}), 201


def _can_edit_visit(visit: SalesVisit, role: RoleEnum, user: User) -> bool:
    if _is_admin(role):
        return True
    if role == RoleEnum.sales and _is_owner(visit, user):
        return visit.approval_status != SalesVisitApprovalStatus.approved
    if role == RoleEnum.outside_manager:
        return False
    return False


@bp.put("/<visit_id>")
def update_visit(visit_id: str):
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    visit = SalesVisit.query.get(visit_id)
    if not visit:
        return jsonify({"ok": False, "error": "Visit not found"}), 404

    if not _can_edit_visit(visit, role, user):
        return jsonify({"ok": False, "error": "Not allowed to edit this visit"}), 403

    payload = request.get_json() or {}

    if _is_admin(role) and payload.get("sales_user_id"):
        try:
            visit.sales_user_id = int(payload["sales_user_id"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid sales_user_id"}), 400

    if payload.get("customer_id") is not None:
        if payload.get("customer_id") == "":
            visit.customer_id = None
        else:
            try:
                cid = int(payload["customer_id"])
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "Invalid customer_id"}), 400
            customer = Customer.query.get(cid)
            if not customer:
                return jsonify({"ok": False, "error": "Customer not found"}), 404
            visit.customer_id = cid
    if payload.get("non_samprox_customer_id") is not None:
        if payload.get("non_samprox_customer_id") == "":
            visit.non_samprox_customer_id = None
        else:
            non_samprox_customer, error = _load_non_samprox_customer(payload.get("non_samprox_customer_id"), role, user)
            if error:
                return error
            visit.non_samprox_customer_id = non_samprox_customer.id
    visit.prospect_name = (payload.get("prospect_name") or "").strip() or None
    if payload.get("visit_date"):
        parsed_date = _parse_date(payload.get("visit_date"))
        if not parsed_date:
            return jsonify({"ok": False, "error": "Invalid visit_date"}), 400
        visit.visit_date = parsed_date
    if payload.get("planned") is not None:
        visit.planned = bool(payload.get("planned"))
    if payload.get("purpose") is not None:
        visit.purpose = payload.get("purpose")
    if payload.get("remarks") is not None:
        visit.remarks = payload.get("remarks")

    if _is_admin(role) and payload.get("approval_status"):
        try:
            visit.approval_status = SalesVisitApprovalStatus(payload.get("approval_status"))
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid approval_status"}), 400
    if _is_admin(role) and payload.get("manual_location_override") is not None:
        visit.manual_location_override = bool(payload.get("manual_location_override"))
    if payload.get("exception_reason") is not None:
        visit.exception_reason = payload.get("exception_reason")

    if _visit_requires_approval(visit):
        if visit.approval_status not in {
            SalesVisitApprovalStatus.approved,
            SalesVisitApprovalStatus.rejected,
        }:
            visit.approval_status = SalesVisitApprovalStatus.pending
    else:
        visit.approval_status = SalesVisitApprovalStatus.not_required

    visit.updated_by = user.id
    db.session.commit()
    return jsonify({"ok": True, "data": _serialize_visit(visit)})


def _apply_check_in_metadata(
    visit: SalesVisit, lat: float, lng: float, ts: Optional[datetime] = None, accuracy_m: Optional[int] = None
) -> None:
    visit.check_in_time = ts or datetime.now(tz=COLOMBO_TZ)
    visit.check_in_lat = Decimal(str(lat))
    visit.check_in_lng = Decimal(str(lng))
    visit.check_in_accuracy_m = accuracy_m
    visit.gps_mismatch = False
    if visit.customer and getattr(visit.customer, "latitude", None) is not None and getattr(visit.customer, "longitude", None) is not None:
        try:
            distance = haversine_distance_meters(
                float(lat),
                float(lng),
                float(visit.customer.latitude),
                float(visit.customer.longitude),
            )
            visit.distance_from_customer_m = distance
            if distance > 200:
                visit.gps_mismatch = True
        except Exception:
            visit.distance_from_customer_m = None
    visit.approval_status = SalesVisitApprovalStatus.pending if _visit_requires_approval(visit) else SalesVisitApprovalStatus.not_required


@bp.post("/<visit_id>/check-in")
def check_in(visit_id: str):
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    visit = SalesVisit.query.get(visit_id)
    if not visit:
        return jsonify({"ok": False, "error": "Visit not found"}), 404

    if visit.check_in_time:
        return jsonify({"ok": False, "error": "Visit already checked-in"}), 409

    if not (_is_owner(visit, user) or _is_admin(role)):
        return jsonify({"ok": False, "error": "Not allowed to check in"}), 403

    payload = request.get_json() or {}
    try:
        lat = float(payload.get("lat"))
        lng = float(payload.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "lat and lng are required"}), 400

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify({"ok": False, "error": "Invalid coordinates"}), 400

    accuracy_raw = payload.get("accuracy_m")
    accuracy = None
    if accuracy_raw is not None:
        try:
            accuracy = int(accuracy_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "accuracy_m must be an integer"}), 400
        if accuracy < 0:
            accuracy = None

    ts = _parse_timestamp(payload.get("timestamp"))
    _apply_check_in_metadata(visit, lat, lng, ts, accuracy_m=accuracy)
    visit.updated_by = user.id
    db.session.commit()
    return jsonify({"ok": True, "data": _serialize_visit(visit)})


def _apply_checkout_metadata(
    visit: SalesVisit, lat: float, lng: float, ts: Optional[datetime] = None, accuracy_m: Optional[int] = None
) -> None:
    now_ts = ts or datetime.now(tz=COLOMBO_TZ)
    visit.check_out_time = now_ts
    visit.check_out_lat = Decimal(str(lat))
    visit.check_out_lng = Decimal(str(lng))
    visit.check_out_accuracy_m = accuracy_m
    if visit.check_in_time:
        if visit.check_in_time.tzinfo is None:
            visit.check_in_time = visit.check_in_time.replace(tzinfo=COLOMBO_TZ)
        if now_ts.tzinfo is None:
            now_ts = now_ts.replace(tzinfo=COLOMBO_TZ)
        delta = now_ts - visit.check_in_time
        visit.duration_minutes = int(round(delta.total_seconds() / 60))
        if visit.duration_minutes < 5:
            visit.short_duration = True
    visit.approval_status = SalesVisitApprovalStatus.pending if _visit_requires_approval(visit) else SalesVisitApprovalStatus.not_required


@bp.post("/<visit_id>/check-out")
def check_out(visit_id: str):
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    visit = SalesVisit.query.get(visit_id)
    if not visit:
        return jsonify({"ok": False, "error": "Visit not found"}), 404

    if not visit.check_in_time:
        return jsonify({"ok": False, "error": "Check-in required before checkout"}), 400

    if visit.check_out_time:
        return jsonify({"ok": False, "error": "Visit already checked-out"}), 409

    if not (_is_owner(visit, user) or _is_admin(role)):
        return jsonify({"ok": False, "error": "Not allowed to check out"}), 403

    payload = request.get_json() or {}
    try:
        lat = float(payload.get("lat"))
        lng = float(payload.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "lat and lng are required"}), 400

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify({"ok": False, "error": "Invalid coordinates"}), 400

    accuracy_raw = payload.get("accuracy_m")
    accuracy = None
    if accuracy_raw is not None:
        try:
            accuracy = int(accuracy_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "accuracy_m must be an integer"}), 400
        if accuracy < 0:
            accuracy = None

    ts = _parse_timestamp(payload.get("timestamp"))
    _apply_checkout_metadata(visit, lat, lng, ts, accuracy_m=accuracy)
    visit.updated_by = user.id
    db.session.commit()
    return jsonify({"ok": True, "data": _serialize_visit(visit)})


@bp.post("/<visit_id>/approve")
def approve_visit(visit_id: str):
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    visit = SalesVisit.query.get(visit_id)
    if not visit:
        return jsonify({"ok": False, "error": "Visit not found"}), 404

    if role not in {RoleEnum.admin, RoleEnum.outside_manager, *SALES_MANAGER_ROLES}:
        return jsonify({"ok": False, "error": "Not authorized to approve"}), 403

    if visit.approval_status != SalesVisitApprovalStatus.pending:
        return jsonify({"ok": False, "error": "No pending approval"}), 409

    if role == RoleEnum.outside_manager and visit.sales_user_id not in _manager_sales_ids(user.id):
        return jsonify({"ok": False, "error": "Not authorized for this visit"}), 403

    payload = request.get_json() or {}
    action = (payload.get("action") or "").upper()
    note = payload.get("approval_note")

    if action not in {"APPROVE", "REJECT"}:
        return jsonify({"ok": False, "error": "action must be APPROVE or REJECT"}), 400

    visit.approval_status = (
        SalesVisitApprovalStatus.approved if action == "APPROVE" else SalesVisitApprovalStatus.rejected
    )
    visit.approved_by = user.id
    visit.approved_at = datetime.now(tz=COLOMBO_TZ)
    visit.approval_note = note
    visit.updated_by = user.id
    db.session.commit()
    return jsonify({"ok": True, "data": _serialize_visit(visit)})


@bp.post("/<visit_id>/attachments")
def add_attachment(visit_id: str):
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    visit = SalesVisit.query.get(visit_id)
    if not visit:
        return jsonify({"ok": False, "error": "Visit not found"}), 404

    if role not in {RoleEnum.admin, *SALES_MANAGER_ROLES, RoleEnum.sales, RoleEnum.outside_manager}:
        return jsonify({"ok": False, "error": "Not authorized to add attachments"}), 403
    if role == RoleEnum.outside_manager:
        return jsonify({"ok": False, "error": "Managers cannot add attachments"}), 403
    if role == RoleEnum.sales and not _is_owner(visit, user):
        return jsonify({"ok": False, "error": "Not allowed for this visit"}), 403

    payload = request.get_json() or {}
    file_url = (payload.get("file_url") or "").strip()
    file_type = (payload.get("file_type") or "").strip() or None
    if not file_url:
        return jsonify({"ok": False, "error": "file_url is required"}), 400

    attachment = SalesVisitAttachment(
        visit_id=visit.id,
        file_url=file_url,
        file_type=file_type,
        uploaded_by=user.id,
    )
    db.session.add(attachment)
    db.session.commit()
    db.session.refresh(visit)
    return jsonify({"ok": True, "data": [_serialize_attachment(att) for att in visit.attachments]}), 201


@bp.post("/team")
def add_team_member():
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    payload = request.get_json() or {}
    try:
        manager_user_id = int(payload.get("manager_user_id") or user.id)
        sales_user_id = int(payload["sales_user_id"])
    except (TypeError, ValueError, KeyError):
        return jsonify({"ok": False, "error": "manager_user_id and sales_user_id are required"}), 400

    if role not in {RoleEnum.admin, RoleEnum.outside_manager, *SALES_MANAGER_ROLES}:
        return jsonify({"ok": False, "error": "Not authorized"}), 403
    if role == RoleEnum.outside_manager and manager_user_id != user.id:
        return jsonify({"ok": False, "error": "Managers can only manage their own teams"}), 403

    mapping = SalesTeamMember(manager_user_id=manager_user_id, sales_user_id=sales_user_id)
    db.session.add(mapping)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "Mapping already exists or invalid users"}), 400

    return jsonify({"ok": True, "data": {"id": str(mapping.id), "manager_user_id": manager_user_id, "sales_user_id": sales_user_id}}), 201


@bp.get("/team")
def list_team_members():
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    if role in {RoleEnum.admin, *SALES_MANAGER_ROLES}:
        mappings = SalesTeamMember.query.all()
    elif role == RoleEnum.outside_manager:
        mappings = SalesTeamMember.query.filter_by(manager_user_id=user.id).all()
    else:
        return jsonify({"ok": False, "error": "Not authorized"}), 403

    user_ids: set[int] = set()
    for mapping in mappings:
        user_ids.add(mapping.sales_user_id)
        user_ids.add(mapping.manager_user_id)

    users = User.query.filter(User.id.in_(user_ids)).all() if user_ids else []
    user_map = {u.id: {"id": u.id, "name": u.name, "role": u.role.value} for u in users}

    data = []
    for mapping in mappings:
        data.append(
            {
                "id": str(mapping.id),
                "manager": user_map.get(mapping.manager_user_id),
                "sales_user": user_map.get(mapping.sales_user_id),
            }
        )

    return jsonify({"ok": True, "data": data})


@bp.delete("/team/<mapping_id>")
def remove_team_member(mapping_id: str):
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    if role not in {RoleEnum.admin, RoleEnum.outside_manager, *SALES_MANAGER_ROLES}:
        return jsonify({"ok": False, "error": "Not authorized"}), 403

    mapping = SalesTeamMember.query.get(mapping_id)
    if not mapping:
        return jsonify({"ok": False, "error": "Mapping not found"}), 404

    if role == RoleEnum.outside_manager and mapping.manager_user_id != user.id:
        return jsonify({"ok": False, "error": "Managers can only update their own teams"}), 403

    db.session.delete(mapping)
    db.session.commit()
    return jsonify({"ok": True, "data": True})
