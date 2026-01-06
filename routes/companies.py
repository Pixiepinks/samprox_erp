from __future__ import annotations

from typing import Optional

from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt, jwt_required

from models import Company, RoleEnum

bp = Blueprint("companies_api", __name__, url_prefix="/api/companies")


def _current_role() -> Optional[RoleEnum]:
    claims = get_jwt() or {}
    try:
        return RoleEnum(claims.get("role"))
    except Exception:
        return None


@bp.before_request
@jwt_required()
def _guard_roles():
    role = _current_role()
    if role not in {
        RoleEnum.sales,
        RoleEnum.outside_manager,
        RoleEnum.sales_manager,
        RoleEnum.sales_executive,
        RoleEnum.admin,
        RoleEnum.finance_manager,
        RoleEnum.production_manager,
    }:
        return jsonify({"ok": False, "error": "Access denied"}), 403


@bp.get("")
def list_companies():
    companies = Company.query.order_by(Company.name.asc()).all()
    return jsonify({"ok": True, "data": [{"id": c.id, "name": c.name, "key": c.key} for c in companies]})
