from __future__ import annotations

from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy import asc

from models import RoleEnum, User

bp = Blueprint("sales_users", __name__, url_prefix="/api/sales-users")

_ALLOWED_ROLES = {
    RoleEnum.admin,
    RoleEnum.finance_manager,
    RoleEnum.production_manager,
    RoleEnum.sales_manager,
}


def _current_role() -> RoleEnum | None:
    claims = get_jwt() or {}
    try:
        return RoleEnum(claims.get("role"))
    except Exception:
        return None


@bp.before_request
@jwt_required()
def _enforce_authentication():
    role = _current_role()
    if role not in _ALLOWED_ROLES:
        return jsonify({"ok": False, "error": "Access denied"}), 403


@bp.get("")
def list_sales_users():
    users = (
        User.query.filter(User.active.is_(True), User.role.in_([RoleEnum.sales, RoleEnum.sales_manager]))
        .order_by(asc(User.name))
        .all()
    )
    return jsonify({"ok": True, "data": [{"id": user.id, "name": user.name} for user in users]})
