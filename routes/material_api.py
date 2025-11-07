"""Material module API routes."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, request

from material import (
    MaterialValidationError,
    create_item,
    create_mrn,
    create_supplier,
    get_briquette_mix_detail,
    get_next_mrn_number,
    get_next_supplier_registration_no,
    get_mrn_detail,
    list_briquette_production_entries,
    list_material_items,
    list_recent_mrns,
    search_suppliers,
    update_briquette_mix,
)
from schemas import MRNSchema, MaterialItemSchema, SupplierSchema

bp = Blueprint("material", __name__, url_prefix="/api/material")

supplier_schema = SupplierSchema()
suppliers_schema = SupplierSchema(many=True)
item_schema = MaterialItemSchema()
items_schema = MaterialItemSchema(many=True)
mrn_schema = MRNSchema()
mrn_list_schema = MRNSchema(many=True)


@bp.get("/suppliers")
def supplier_search():
    query = request.args.get("search")
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    suppliers = search_suppliers(query, limit=limit)
    return jsonify(suppliers_schema.dump(suppliers))


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
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))
    mrns = list_recent_mrns(search=search, limit=limit)
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


@bp.get("/briquette-production")
def list_briquette_production():
    try:
        limit = int(request.args.get("limit", 30))
    except (TypeError, ValueError):
        limit = 30
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
