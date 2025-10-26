"""Material module API routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from material import (
    MaterialValidationError,
    create_item,
    create_mrn,
    create_supplier,
    get_mrn_detail,
    list_material_items,
    list_recent_mrns,
    search_suppliers,
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


@bp.get("/mrn/<mrn_id>")
def get_mrn(mrn_id: str):
    try:
        mrn = get_mrn_detail(mrn_id)
    except MaterialValidationError as exc:
        status = 404 if exc.errors.get("id") == "MRN not found." else 400
        return jsonify({"errors": exc.errors}), status
    return jsonify(mrn_schema.dump(mrn))
