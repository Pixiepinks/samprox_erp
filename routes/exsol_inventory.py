from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy.exc import SQLAlchemyError

from exsol_inventory import ExsolInventoryError, list_exsol_items, seed_exsol_defaults, upsert_exsol_item
from extensions import db
from models import Company, ExsolInventoryItem, RoleEnum, normalize_role
from schemas import ExsolInventoryItemSchema

bp = Blueprint("exsol_inventory", __name__, url_prefix="/api/exsol")

item_schema = ExsolInventoryItemSchema()
items_schema = ExsolInventoryItemSchema(many=True)

EXSOL_COMPANY_NAME = "Exsol Engineering (Pvt) Ltd"


def _has_exsol_access(require_admin: bool = False) -> bool:
    try:
        claims = get_jwt()
    except Exception:
        return False

    role_raw = claims.get("role")
    company_key = (claims.get("company_key") or claims.get("company") or "").strip().lower()

    role = normalize_role(role_raw)

    if role == RoleEnum.admin:
        return True

    if require_admin:
        return False

    if company_key and company_key != "exsol-engineering":
        return False

    return role in {RoleEnum.sales_manager, RoleEnum.sales_executive}


def _get_exsol_company_id() -> int | None:
    company = Company.query.filter(Company.name == EXSOL_COMPANY_NAME).one_or_none()
    return company.id if company else None


def _build_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


@bp.get("/inventory-items")
@jwt_required()
def list_inventory_items():
    if not _has_exsol_access():
        return _build_error("Access denied", 403)

    company_id = _get_exsol_company_id()
    if not company_id:
        return _build_error("Exsol company not configured.", 500)

    query = (request.args.get("q") or "").strip()
    stmt = ExsolInventoryItem.query.filter(
        ExsolInventoryItem.company_id == company_id,
        ExsolInventoryItem.is_active.is_(True),
    )
    if query:
        like = f"%{query}%"
        stmt = stmt.filter(
            (ExsolInventoryItem.item_code.ilike(like)) | (ExsolInventoryItem.item_name.ilike(like))
        )

    items = stmt.order_by(ExsolInventoryItem.item_code.asc()).all()
    return jsonify(items_schema.dump(items))


@bp.post("/inventory-items")
@jwt_required()
def create_inventory_item():
    if not _has_exsol_access(require_admin=True):
        return _build_error("Admins only", 403)

    payload = request.get_json(silent=True) or {}
    try:
        item = upsert_exsol_item(payload)
    except ExsolInventoryError as exc:
        return jsonify({"errors": exc.errors}), 400
    except SQLAlchemyError:
        return _build_error("Unable to save Exsol item right now.", 500)
    return jsonify(item_schema.dump(item)), 201


@bp.post("/inventory-items/bulk")
@jwt_required()
def bulk_create_inventory_items():
    if not _has_exsol_access(require_admin=True):
        return _build_error("Admins only", 403)

    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        return _build_error("Items payload is required.", 400)

    errors: list[dict[str, str | int]] = []
    saved = 0
    try:
        for idx, item_payload in enumerate(items):
            try:
                upsert_exsol_item(item_payload, session=db.session)
                saved += 1
            except ExsolInventoryError as exc:
                errors.append({"index": idx, "errors": exc.errors})

        if errors:
            db.session.rollback()
            return jsonify({"errors": errors}), 400

        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _build_error("Unable to save Exsol items right now.", 500)

    return jsonify({"saved": saved})


@bp.get("/stock-items")
@jwt_required()
def list_stock_items():
    """Legacy endpoint for Exsol stock items."""
    if not _has_exsol_access():
        return _build_error("Access denied", 403)

    query = request.args.get("q")
    try:
        items = list_exsol_items(search=query)
    except ExsolInventoryError as exc:
        return jsonify({"errors": exc.errors}), 400
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
    """Legacy endpoint for Exsol stock items."""
    if not _has_exsol_access(require_admin=True):
        return _build_error("Admins only", 403)

    payload = request.get_json(silent=True) or {}
    try:
        item = upsert_exsol_item(payload)
    except ExsolInventoryError as exc:
        return jsonify({"errors": exc.errors}), 400
    except SQLAlchemyError:
        return _build_error("Unable to save Exsol item right now.", 500)
    return jsonify(item_schema.dump(item)), 201


@bp.post("/seed")
@jwt_required()
def seed_stock_items():
    """Legacy endpoint for Exsol stock item defaults."""
    if not _has_exsol_access(require_admin=True):
        return _build_error("Admins only", 403)

    try:
        count = seed_exsol_defaults()
    except ExsolInventoryError as exc:
        return jsonify({"errors": exc.errors}), 400
    except SQLAlchemyError:
        return _build_error("Unable to seed Exsol inventory right now.", 500)
    return jsonify({"seeded": count})
