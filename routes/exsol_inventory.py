from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy.exc import SQLAlchemyError

from exsol_inventory import ExsolInventoryError, list_exsol_items, seed_exsol_defaults, upsert_exsol_item
from exsol_storage import ExsolStorageUnavailable
from models import RoleEnum
from schemas import ExsolStockItemSchema

bp = Blueprint("exsol_inventory", __name__, url_prefix="/api/exsol")

item_schema = ExsolStockItemSchema()
items_schema = ExsolStockItemSchema(many=True)


def _has_exsol_access(require_admin: bool = False) -> bool:
    try:
        claims = get_jwt()
    except Exception:
        return False

    role_raw = claims.get("role")
    company_key = (claims.get("company_key") or claims.get("company") or "").strip().lower()

    try:
        role = RoleEnum(role_raw)
    except Exception:
        role = None

    if role == RoleEnum.admin:
        return True

    if require_admin:
        return False

    if company_key and company_key != "exsol-engineering":
        return False

    return role in {RoleEnum.sales_manager, RoleEnum.sales_executive} or company_key == "exsol-engineering"


def _build_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


@bp.get("/stock-items")
@jwt_required()
def list_stock_items():
    if not _has_exsol_access():
        return _build_error("Access denied", 403)

    query = request.args.get("q")
    try:
        items = list_exsol_items(search=query)
    except ExsolStorageUnavailable as exc:
        return _build_error(str(exc), 503)
    except SQLAlchemyError:
        return _build_error("Unable to load Exsol inventory right now.", 500)
    return jsonify(items_schema.dump(items))


@bp.get("/stock-items/search")
@jwt_required()
def search_stock_items():
    return list_stock_items()


@bp.post("/stock-items")
@jwt_required()
def create_stock_item():
    if not _has_exsol_access(require_admin=True):
        return _build_error("Admins only", 403)

    payload = request.get_json(silent=True) or {}
    try:
        item = upsert_exsol_item(payload)
    except ExsolInventoryError as exc:
        return jsonify({"errors": exc.errors}), 400
    except ExsolStorageUnavailable as exc:
        return _build_error(str(exc), 503)
    except SQLAlchemyError:
        return _build_error("Unable to save Exsol item right now.", 500)
    return jsonify(item_schema.dump(item)), 201


@bp.post("/seed")
@jwt_required()
def seed_stock_items():
    if not _has_exsol_access(require_admin=True):
        return _build_error("Admins only", 403)

    try:
        count = seed_exsol_defaults()
    except ExsolStorageUnavailable as exc:
        return _build_error(str(exc), 503)
    except SQLAlchemyError:
        return _build_error("Unable to seed Exsol inventory right now.", 500)
    return jsonify({"seeded": count})
