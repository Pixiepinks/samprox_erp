"""REST endpoints for machine assets, parts, idle events and suppliers."""
from datetime import datetime, date

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    MachineAsset,
    MachinePart,
    MachinePartReplacement,
    MachineIdleEvent,
    RoleEnum,
    ServiceSupplier,
)
from schemas import (
    MachineAssetSchema,
    MachinePartReplacementSchema,
    MachinePartSchema,
    MachineIdleEventSchema,
    ServiceSupplierSchema,
)

bp = Blueprint("machines", __name__, url_prefix="/api/machines")

asset_schema = MachineAssetSchema()
assets_schema = MachineAssetSchema(many=True)
part_schema = MachinePartSchema()
parts_schema = MachinePartSchema(many=True)
replacement_schema = MachinePartReplacementSchema()
replacements_schema = MachinePartReplacementSchema(many=True)
idle_event_schema = MachineIdleEventSchema()
idle_events_schema = MachineIdleEventSchema(many=True)
supplier_schema = ServiceSupplierSchema()
suppliers_schema = ServiceSupplierSchema(many=True)


def require_role(*roles: RoleEnum) -> bool:
    """Return ``True`` if the current JWT belongs to one of the roles."""
    claims = get_jwt()
    try:
        current_role = RoleEnum(claims.get("role"))
    except (ValueError, TypeError):
        return False
    return current_role in roles


def _parse_date(value, *, field_name: str):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid date for {field_name}")


def _parse_datetime(value, *, field_name: str):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid datetime for {field_name}")


@bp.get("/assets")
@jwt_required()
def list_assets():
    """Return all machine assets sorted alphabetically."""
    assets = MachineAsset.query.order_by(MachineAsset.name.asc()).all()
    return jsonify(assets_schema.dump(assets))


@bp.post("/assets")
@jwt_required()
def create_asset():
    if not require_role(RoleEnum.production_manager, RoleEnum.admin):
        return jsonify({"msg": "Only Production Managers or Admins can create assets."}), 403

    payload = request.get_json() or {}
    code = (payload.get("code") or "").strip()
    name = (payload.get("name") or "").strip()
    if not code or not name:
        return jsonify({"msg": "Code and name are required."}), 400

    asset = MachineAsset(
        code=code,
        name=name,
        category=(payload.get("category") or None),
        location=(payload.get("location") or None),
        manufacturer=(payload.get("manufacturer") or None),
        model_number=(payload.get("model_number") or None),
        serial_number=(payload.get("serial_number") or None),
        installed_on=_parse_date(payload.get("installed_on"), field_name="installed_on"),
        status=(payload.get("status") or None),
        notes=(payload.get("notes") or None),
    )

    db.session.add(asset)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"msg": "Asset code already exists."}), 409

    return jsonify(asset_schema.dump(asset)), 201


@bp.get("/assets/<int:asset_id>")
@jwt_required()
def get_asset(asset_id: int):
    asset = MachineAsset.query.get_or_404(asset_id)
    return jsonify(asset_schema.dump(asset))


@bp.post("/assets/<int:asset_id>/parts")
@jwt_required()
def create_part(asset_id: int):
    if not require_role(RoleEnum.production_manager, RoleEnum.admin, RoleEnum.maintenance_manager):
        return jsonify({"msg": "You do not have permission to add parts."}), 403

    asset = MachineAsset.query.get_or_404(asset_id)
    payload = request.get_json() or {}

    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"msg": "Part name is required."}), 400

    expected_life = payload.get("expected_life_hours")
    if expected_life in ("", None):
        expected_life = None
    else:
        try:
            expected_life = int(expected_life)
        except (TypeError, ValueError):
            return jsonify({"msg": "Expected life must be a number."}), 400

    part = MachinePart(
        asset=asset,
        name=name,
        part_number=(payload.get("part_number") or None),
        description=(payload.get("description") or None),
        expected_life_hours=expected_life,
        notes=(payload.get("notes") or None),
    )

    db.session.add(part)
    db.session.commit()
    return jsonify(part_schema.dump(part)), 201


@bp.get("/assets/<int:asset_id>/parts")
@jwt_required()
def list_parts(asset_id: int):
    MachineAsset.query.get_or_404(asset_id)  # ensure exists
    parts = MachinePart.query.filter_by(asset_id=asset_id).order_by(MachinePart.name.asc()).all()
    return jsonify(parts_schema.dump(parts))


@bp.post("/parts/<int:part_id>/replacements")
@jwt_required()
def log_replacement(part_id: int):
    if not require_role(RoleEnum.maintenance_manager, RoleEnum.admin, RoleEnum.production_manager):
        return jsonify({"msg": "You do not have permission to log replacements."}), 403

    part = MachinePart.query.get_or_404(part_id)
    payload = request.get_json() or {}

    try:
        replaced_on = _parse_date(payload.get("replaced_on"), field_name="replaced_on")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    if not replaced_on:
        replaced_on = date.today()

    replacement = MachinePartReplacement(
        part=part,
        replaced_on=replaced_on,
        replaced_by=(payload.get("replaced_by") or None),
        reason=(payload.get("reason") or None),
        notes=(payload.get("notes") or None),
    )
    db.session.add(replacement)
    db.session.commit()
    return jsonify(replacement_schema.dump(replacement)), 201


@bp.get("/parts/<int:part_id>/replacements")
@jwt_required()
def list_replacements(part_id: int):
    MachinePart.query.get_or_404(part_id)
    replacements = (
        MachinePartReplacement.query.filter_by(part_id=part_id)
        .order_by(MachinePartReplacement.replaced_on.desc())
        .all()
    )
    return jsonify(replacements_schema.dump(replacements))


@bp.get("/idle-events")
@jwt_required()
def list_idle_events():
    query = MachineIdleEvent.query.join(MachineAsset)
    asset_id = request.args.get("asset_id")
    if asset_id:
        try:
            asset_id = int(asset_id)
        except (TypeError, ValueError):
            return jsonify({"msg": "Invalid asset_id"}), 400
        query = query.filter(MachineIdleEvent.asset_id == asset_id)
    events = query.order_by(MachineIdleEvent.started_at.desc()).all()
    return jsonify(idle_events_schema.dump(events))


@bp.post("/idle-events")
@jwt_required()
def create_idle_event():
    if not require_role(RoleEnum.production_manager, RoleEnum.admin, RoleEnum.maintenance_manager):
        return jsonify({"msg": "You do not have permission to log idle time."}), 403

    payload = request.get_json() or {}

    asset_id = payload.get("asset_id")
    try:
        asset_id = int(asset_id)
    except (TypeError, ValueError):
        return jsonify({"msg": "asset_id is required"}), 400

    asset = MachineAsset.query.get_or_404(asset_id)

    try:
        started_at = _parse_datetime(payload.get("started_at"), field_name="started_at")
        ended_at = _parse_datetime(payload.get("ended_at"), field_name="ended_at")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    if not started_at:
        return jsonify({"msg": "started_at is required"}), 400

    if ended_at and ended_at < started_at:
        return jsonify({"msg": "ended_at must be after started_at"}), 400

    event = MachineIdleEvent(
        asset=asset,
        started_at=started_at,
        ended_at=ended_at,
        reason=(payload.get("reason") or None),
        notes=(payload.get("notes") or None),
    )

    db.session.add(event)
    db.session.commit()
    return jsonify(idle_event_schema.dump(event)), 201


@bp.get("/service-suppliers")
@jwt_required()
def list_suppliers():
    suppliers = ServiceSupplier.query.order_by(ServiceSupplier.name.asc()).all()
    return jsonify(suppliers_schema.dump(suppliers))


@bp.post("/service-suppliers")
@jwt_required()
def create_supplier():
    if not require_role(RoleEnum.production_manager, RoleEnum.admin):
        return jsonify({"msg": "You do not have permission to create suppliers."}), 403

    payload = request.get_json() or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"msg": "Supplier name is required."}), 400

    supplier = ServiceSupplier(
        name=name,
        contact_person=(payload.get("contact_person") or None),
        phone=(payload.get("phone") or None),
        email=(payload.get("email") or None),
        services_offered=(payload.get("services_offered") or None),
        preferred_assets=(payload.get("preferred_assets") or None),
        notes=(payload.get("notes") or None),
    )

    db.session.add(supplier)
    db.session.commit()
    return jsonify(supplier_schema.dump(supplier)), 201
