from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo
import uuid

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Company, NonSamproxCustomer, RoleEnum, SalesTeamMember, User

bp = Blueprint("non_samprox_customers", __name__, url_prefix="/api/non-samprox-customers")
COLOMBO_TZ = ZoneInfo("Asia/Colombo")


def _now_colombo() -> datetime:
    return datetime.now(tz=COLOMBO_TZ)


def generate_non_samprox_customer_code(*, lock: bool = True) -> str:
    now = _now_colombo()
    year_suffix = now.year % 100
    prefix = f"{year_suffix:02d}"

    query = (
        NonSamproxCustomer.query.filter(NonSamproxCustomer.customer_code.like(f"{prefix}____"))
        .order_by(NonSamproxCustomer.customer_code.desc())
    )
    try:
        latest = query.with_for_update().first() if lock and hasattr(query, "with_for_update") else query.first()
    except Exception:
        latest = query.first()

    next_seq = 1
    if latest and latest.customer_code and latest.customer_code.startswith(prefix):
        try:
            next_seq = int(latest.customer_code[2:]) + 1
        except ValueError:
            next_seq = 1

    return f"{year_suffix:02d}{next_seq:04d}"


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


def _current_role() -> Optional[RoleEnum]:
    claims = get_jwt() or {}
    try:
        return RoleEnum(claims.get("role"))
    except Exception:
        return None


def _manager_sales_ids(manager_id: int) -> set[int]:
    rows = SalesTeamMember.query.filter_by(manager_user_id=manager_id).all()
    return {row.sales_user_id for row in rows}


def _serialize_customer(customer: NonSamproxCustomer) -> dict[str, Any]:
    return {
        "id": str(customer.id),
        "customer_code": customer.customer_code,
        "customer_name": customer.customer_name,
        "area_code": customer.area_code,
        "city": customer.city,
        "district": customer.district,
        "province": customer.province,
        "managed_by": customer.managed_by_label or getattr(customer.managed_by, "name", None),
        "company": customer.company_label or getattr(customer.company, "name", None),
        "managed_by_user_id": customer.managed_by_user_id,
        "managed_by_name": getattr(customer.managed_by, "name", None),
        "company_id": customer.company_id,
        "company_name": getattr(customer.company, "name", None),
        "is_active": bool(customer.is_active),
    }


@bp.before_request
@jwt_required()
def _guard_roles():
    role = _current_role()
    if role not in {RoleEnum.sales, RoleEnum.outside_manager, RoleEnum.admin}:
        return jsonify({"ok": False, "error": "Access denied"}), 403


def _scoped_query(user: User, role: RoleEnum):
    query = NonSamproxCustomer.query
    if role == RoleEnum.sales:
        query = query.filter(NonSamproxCustomer.managed_by_user_id == user.id)
    elif role == RoleEnum.outside_manager:
        team_ids = _manager_sales_ids(user.id) | {user.id}
        query = query.filter(NonSamproxCustomer.managed_by_user_id.in_(team_ids or {-1}))
    return query


def _load_customer(customer_id: object, user: User, role: RoleEnum):
    try:
        parsed_id = str(uuid.UUID(str(customer_id)))
    except (TypeError, ValueError):
        return None, (jsonify({"ok": False, "error": "Invalid customer id"}), 400)

    customer = NonSamproxCustomer.query.get(parsed_id)
    if not customer:
        return None, (jsonify({"ok": False, "error": "Non Samprox customer not found"}), 404)

    if role == RoleEnum.sales and customer.managed_by_user_id != user.id:
        return None, (jsonify({"ok": False, "error": "Not authorized for this customer"}), 403)

    if role == RoleEnum.outside_manager:
        if customer.managed_by_user_id not in (_manager_sales_ids(user.id) | {user.id}):
            return None, (jsonify({"ok": False, "error": "Not authorized for this customer"}), 403)

    return customer, None


def _validate_company(company_id_raw: object):
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        return None, (jsonify({"ok": False, "error": "Invalid company_id"}), 400)

    company = Company.query.get(company_id)
    if not company:
        return None, (jsonify({"ok": False, "error": "Company not found"}), 404)
    return company, None


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {"true", "1", "yes", "y"}:
            return True
        if value.lower() in {"false", "0", "no", "n"}:
            return False
    try:
        return bool(int(value))
    except Exception:
        return bool(value)


@bp.get("")
def list_customers():
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    query = _scoped_query(user, role)

    search = (request.args.get("q") or "").strip()
    managed_by_param = request.args.get("managed_by")
    company_param = request.args.get("company_id")

    if managed_by_param:
        if role not in {RoleEnum.outside_manager, RoleEnum.admin}:
            return jsonify({"ok": False, "error": "Not authorized to filter by managed_by"}), 403
        try:
            target_managed_by = int(managed_by_param)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid managed_by parameter"}), 400
        if role == RoleEnum.outside_manager and target_managed_by not in (_manager_sales_ids(user.id) | {user.id}):
            return jsonify({"ok": False, "error": "Not authorized for this manager"}), 403
        query = query.filter(NonSamproxCustomer.managed_by_user_id == target_managed_by)

    if company_param:
        try:
            company_id = int(company_param)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid company_id"}), 400
        query = query.filter(NonSamproxCustomer.company_id == company_id)

    if search:
        ilike = f"%{search}%"
        query = query.filter(
            or_(
                NonSamproxCustomer.customer_code.ilike(ilike),
                NonSamproxCustomer.customer_name.ilike(ilike),
                NonSamproxCustomer.city.ilike(ilike),
                NonSamproxCustomer.district.ilike(ilike),
            )
        )

    customers = query.order_by(NonSamproxCustomer.customer_code.asc()).all()
    return jsonify({"ok": True, "data": [_serialize_customer(c) for c in customers]})


@bp.get("/<customer_id>")
def get_customer(customer_id):
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    customer, error = _load_customer(customer_id, user, role)
    if error:
        return error

    return jsonify({"ok": True, "data": _serialize_customer(customer)})


@bp.get("/next-code")
def preview_next_code():
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    next_code = generate_non_samprox_customer_code(lock=False)
    return jsonify({"ok": True, "data": {"next_code": next_code}, "next_code": next_code})


@bp.post("")
def create_customer():
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    payload = request.get_json() or {}
    customer_name = (payload.get("customer_name") or "").strip()
    area_code = (payload.get("area_code") or "").strip() or None
    city = (payload.get("city") or "").strip() or None
    district = (payload.get("district") or "").strip() or None
    province = (payload.get("province") or "").strip() or None
    company_raw = payload.get("company_id")

    if not customer_name:
        return jsonify({"ok": False, "error": "customer_name is required"}), 400

    try:
        company_id = int(company_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid company_id"}), 400

    company = Company.query.get(company_id)
    if not company:
        return jsonify({"ok": False, "error": "Company not found"}), 404

    managed_by = user.id
    managed_by_raw = payload.get("managed_by_user_id")

    if role == RoleEnum.sales:
        managed_by = user.id
    elif role == RoleEnum.outside_manager:
        team_ids = _manager_sales_ids(user.id) | {user.id}
        if managed_by_raw is not None:
            try:
                candidate = int(managed_by_raw)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "Invalid managed_by_user_id"}), 400
            if candidate not in team_ids:
                return jsonify({"ok": False, "error": "Not authorized for this managed_by_user_id"}), 403
            managed_by = candidate
        else:
            managed_by = user.id
    elif role == RoleEnum.admin and managed_by_raw is not None:
        try:
            managed_by = int(managed_by_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid managed_by_user_id"}), 400

    attempts = 0
    while attempts < 2:
        attempts += 1
        customer_code = generate_non_samprox_customer_code()
        customer = NonSamproxCustomer(
            customer_code=customer_code,
            customer_name=customer_name,
            area_code=area_code,
            city=city,
            district=district,
            province=province,
            managed_by_user_id=managed_by,
            company_id=company.id,
        )
        db.session.add(customer)
        try:
            db.session.commit()
            return jsonify({"ok": True, "data": _serialize_customer(customer)}), 201
        except IntegrityError:
            db.session.rollback()
            if attempts >= 2:
                return jsonify({"ok": False, "error": "Customer code already exists, please retry"}), 400

    return jsonify({"ok": False, "error": "Unable to create customer"}), 500


@bp.put("/<customer_id>")
def update_customer(customer_id):
    role = _current_role()
    user = _current_user()
    if not user or not role:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    customer, error = _load_customer(customer_id, user, role)
    if error:
        return error

    payload = request.get_json() or {}

    # Prevent changing code even if sent
    payload.pop("customer_code", None)

    allowed_fields = {
        "customer_name",
        "area_code",
        "city",
        "district",
        "province",
        "company_id",
    }

    if role in {RoleEnum.outside_manager, RoleEnum.admin}:
        allowed_fields |= {"managed_by_user_id", "is_active"}

    updates = {key: payload.get(key) for key in allowed_fields if key in payload}

    if "customer_name" in updates:
        if not (updates["customer_name"] or "").strip():
            return jsonify({"ok": False, "error": "customer_name is required"}), 400
        customer.customer_name = updates["customer_name"].strip()

    if "area_code" in updates:
        customer.area_code = (updates["area_code"] or "").strip() or None
    if "city" in updates:
        customer.city = (updates["city"] or "").strip() or None
    if "district" in updates:
        customer.district = (updates["district"] or "").strip() or None
    if "province" in updates:
        customer.province = (updates["province"] or "").strip() or None

    if "company_id" in updates:
        company, err = _validate_company(updates["company_id"])
        if err:
            return err
        customer.company_id = company.id

    if "managed_by_user_id" in updates:
        if role == RoleEnum.sales:
            return jsonify({"ok": False, "error": "Not authorized to change managed_by"}), 403
        try:
            managed_by = int(updates["managed_by_user_id"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid managed_by_user_id"}), 400
        if role == RoleEnum.outside_manager:
            if managed_by not in (_manager_sales_ids(user.id) | {user.id}):
                return jsonify({"ok": False, "error": "Not authorized for this managed_by_user_id"}), 403
        customer.managed_by_user_id = managed_by

    if "is_active" in updates:
        if role == RoleEnum.sales:
            return jsonify({"ok": False, "error": "Not authorized to change is_active"}), 403
        customer.is_active = _parse_bool(updates["is_active"])

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        details = str(exc.orig) if hasattr(exc, "orig") else None
        return jsonify({"ok": False, "error": "Unable to update customer", "details": details}), 400

    return jsonify({"ok": True, "data": _serialize_customer(customer)})
