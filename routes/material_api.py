"""Material module API routes."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required

from material import (
    DEFAULT_BRIQUETTE_ENTRY_LIMIT,
    MaterialValidationError,
    calculate_stock_status,
    create_item,
    create_mrn,
    create_supplier,
    get_supplier_detail,
    get_briquette_mix_detail,
    get_next_mrn_number,
    get_next_supplier_registration_no,
    get_mrn_detail,
    list_briquette_production_entries,
    list_material_items,
    list_recent_mrns,
    search_suppliers,
    update_mrn,
    update_briquette_mix,
    update_supplier,
)
from models import RoleEnum
from schemas import MRNSchema, MaterialItemSchema, SupplierSchema

bp = Blueprint("material", __name__, url_prefix="/api/material")

supplier_schema = SupplierSchema()
suppliers_schema = SupplierSchema(many=True)
item_schema = MaterialItemSchema()
items_schema = MaterialItemSchema(many=True)
mrn_schema = MRNSchema()
mrn_list_schema = MRNSchema(many=True)


def require_role(*roles: RoleEnum) -> bool:
    """Return ``True`` if the current JWT belongs to one of the roles."""

    claims = get_jwt()
    try:
        current_role = RoleEnum(claims.get("role"))
    except (ValueError, TypeError):
        return False
    return current_role in roles


@bp.get("/suppliers")
def supplier_search():
    query = request.args.get("search")
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    suppliers = search_suppliers(query, limit=limit)
    return jsonify(suppliers_schema.dump(suppliers))


@bp.get("/suppliers/<string:supplier_id>")
@jwt_required()
def supplier_detail(supplier_id: str):
    if not require_role(RoleEnum.admin, RoleEnum.production_manager):
        return jsonify({"msg": "You do not have permission to view suppliers."}), 403

    try:
        supplier = get_supplier_detail(supplier_id)
    except MaterialValidationError as exc:
        status = 404 if exc.errors.get("id") == "Supplier not found." else 400
        return jsonify({"errors": exc.errors}), status
    return jsonify(supplier_schema.dump(supplier))


@bp.get("/suppliers/next-registration-number")
def supplier_next_registration_number():
    registration_no = get_next_supplier_registration_no()
    return jsonify({"registration_no": registration_no})


@bp.post("/suppliers")
def supplier_create():
    payload = request.get_json(silent=True) or {}
    try:
        supplier = create_supplier(payload)
    except MaterialValidationError as exc:
        return jsonify({"errors": exc.errors}), 400
    return jsonify(supplier_schema.dump(supplier)), 201


@bp.put("/suppliers/<string:supplier_id>")
@jwt_required()
def supplier_update(supplier_id: str):
    if not require_role(RoleEnum.admin, RoleEnum.production_manager):
        return jsonify({"msg": "You do not have permission to edit suppliers."}), 403

    payload = request.get_json(silent=True) or {}
    try:
        supplier = update_supplier(supplier_id, payload)
    except MaterialValidationError as exc:
        status = 404 if exc.errors.get("id") == "Supplier not found." else 400
        return jsonify({"errors": exc.errors}), status
    return jsonify(supplier_schema.dump(supplier))


@bp.get("/items")
def list_items():
    query = request.args.get("search")
    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 200))
    items = list_material_items(search=query, limit=limit)
    return jsonify(items_schema.dump(items))


@bp.post("/items")
def create_item_entry():
    payload = request.get_json(silent=True) or {}
    try:
        item = create_item(payload)
    except MaterialValidationError as exc:
        return jsonify({"errors": exc.errors}), 400
    return jsonify(item_schema.dump(item)), 201


@bp.get("/mrn")
def list_mrn_entries():
    search = request.args.get("q")
    start_date_raw = request.args.get("start_date")
    end_date_raw = request.args.get("end_date")
    raw_limit = request.args.get("limit")

    if raw_limit is None and (start_date_raw or end_date_raw):
        limit = None
    else:
        try:
            limit = int(raw_limit or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 100))
    date_errors: dict[str, str] = {}
    start_date = None
    end_date = None

    if start_date_raw:
        try:
            start_date = date.fromisoformat(start_date_raw)
        except ValueError:
            date_errors["start_date"] = "Invalid start date. Use YYYY-MM-DD."

    if end_date_raw:
        try:
            end_date = date.fromisoformat(end_date_raw)
        except ValueError:
            date_errors["end_date"] = "Invalid end date. Use YYYY-MM-DD."

    if not date_errors and start_date and end_date and start_date > end_date:
        date_errors["date_range"] = "Start date must be on or before end date."

    if date_errors:
        return jsonify({"errors": date_errors}), 400

    mrns = list_recent_mrns(
        search=search,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
    )
    return jsonify(mrn_list_schema.dump(mrns))


@bp.post("/mrn")
def create_mrn_entry():
    payload = request.get_json(silent=True) or {}
    try:
        mrn = create_mrn(payload)
    except MaterialValidationError as exc:
        return jsonify({"errors": exc.errors}), 400
    return jsonify(mrn_schema.dump(mrn)), 201


@bp.get("/mrn/next-number")
def mrn_next_number():
    next_number = get_next_mrn_number()
    return jsonify({"mrn_no": next_number})


@bp.get("/mrn/<mrn_id>")
def get_mrn(mrn_id: str):
    try:
        mrn = get_mrn_detail(mrn_id)
    except MaterialValidationError as exc:
        status = 404 if exc.errors.get("id") == "MRN not found." else 400
        return jsonify({"errors": exc.errors}), status
    return jsonify(mrn_schema.dump(mrn))


@bp.put("/mrn/<mrn_id>")
def update_mrn_entry(mrn_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        mrn = update_mrn(mrn_id, payload)
    except MaterialValidationError as exc:
        status = 404 if exc.errors.get("id") == "MRN not found." else 400
        return jsonify({"errors": exc.errors}), status
    return jsonify(mrn_schema.dump(mrn))


@bp.get("/briquette-production")
def list_briquette_production():
    try:
        limit = int(request.args.get("limit", DEFAULT_BRIQUETTE_ENTRY_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_BRIQUETTE_ENTRY_LIMIT
    data = list_briquette_production_entries(limit=limit)
    return jsonify(data)


@bp.get("/briquette-production/<date_value>")
def get_briquette_mix(date_value: str):
    if not date_value:
        return jsonify({"msg": "Date is required."}), 400
    try:
        target_date = date.fromisoformat(date_value)
    except ValueError:
        return jsonify({"msg": "Invalid date. Use YYYY-MM-DD."}), 400
    data = get_briquette_mix_detail(target_date)
    return jsonify(data)


@bp.post("/briquette-production/<date_value>")
def save_briquette_mix(date_value: str):
    if not date_value:
        return jsonify({"msg": "Date is required."}), 400
    try:
        target_date = date.fromisoformat(date_value)
    except ValueError:
        return jsonify({"msg": "Invalid date. Use YYYY-MM-DD."}), 400

    payload = request.get_json(silent=True) or {}
    try:
        data = update_briquette_mix(target_date, payload)
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400
    return jsonify(data)


@bp.get("/stock-status")
def get_stock_status():
    raw_date = request.args.get("as_of")
    if raw_date:
        try:
            target_date = date.fromisoformat(raw_date)
        except ValueError:
            return jsonify({"msg": "Invalid date. Use YYYY-MM-DD."}), 400
    else:
        target_date = date.today()

    data = calculate_stock_status(target_date)
    return jsonify(data)
