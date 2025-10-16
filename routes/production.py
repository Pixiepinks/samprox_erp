"""REST endpoints for manufacturing daily production tracking."""

from datetime import date as dt_date

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required

from extensions import db
from models import DailyProductionEntry, MachineAsset, RoleEnum
from schemas import DailyProductionEntrySchema


bp = Blueprint("production", __name__, url_prefix="/api/production")

entry_schema = DailyProductionEntrySchema()


def require_role(*roles: RoleEnum) -> bool:
    """Return ``True`` if the current JWT belongs to one of the roles."""

    claims = get_jwt()
    try:
        current_role = RoleEnum(claims.get("role"))
    except (TypeError, ValueError):
        return False
    return current_role in roles


def _parse_date(value, *, field_name: str):
    if not value:
        return None
    if isinstance(value, dt_date):
        return value
    try:
        return dt_date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid date for {field_name}")


def _get_asset(payload):
    asset_id = payload.get("asset_id")
    machine_code = (payload.get("machine_code") or "").strip()

    asset = None
    if asset_id is not None:
        try:
            asset_id = int(asset_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid asset_id")
        asset = MachineAsset.query.get(asset_id)
    elif machine_code:
        asset = MachineAsset.query.filter(
            MachineAsset.code.ilike(machine_code)
        ).first()

    if not asset:
        raise LookupError("Machine asset not found")

    return asset


@bp.post("/daily")
@jwt_required()
def upsert_daily_production():
    if not require_role(RoleEnum.production_manager, RoleEnum.admin):
        return jsonify({"msg": "You do not have permission to record production."}), 403

    payload = request.get_json() or {}

    try:
        production_date = _parse_date(payload.get("date"), field_name="date") or dt_date.today()
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    try:
        asset = _get_asset(payload)
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"msg": str(exc)}), 404

    try:
        hour_no = int(payload.get("hour_no"))
    except (TypeError, ValueError):
        return jsonify({"msg": "hour_no must be an integer between 1 and 24."}), 400

    if hour_no < 1 or hour_no > 24:
        return jsonify({"msg": "hour_no must be between 1 and 24."}), 400

    try:
        quantity_tons = float(payload.get("quantity_tons", 0))
    except (TypeError, ValueError):
        return jsonify({"msg": "quantity_tons must be a number."}), 400

    if quantity_tons < 0:
        return jsonify({"msg": "quantity_tons cannot be negative."}), 400

    entry = DailyProductionEntry.query.filter_by(
        date=production_date,
        asset_id=asset.id,
        hour_no=hour_no,
    ).first()

    status_code = 200
    if entry:
        entry.quantity_tons = quantity_tons
    else:
        entry = DailyProductionEntry(
            date=production_date,
            asset=asset,
            hour_no=hour_no,
            quantity_tons=quantity_tons,
        )
        db.session.add(entry)
        status_code = 201

    db.session.commit()

    return jsonify(entry_schema.dump(entry)), status_code


@bp.get("/daily")
@jwt_required()
def get_daily_production():
    if not require_role(RoleEnum.production_manager, RoleEnum.admin, RoleEnum.maintenance_manager):
        return jsonify({"msg": "You do not have permission to view production."}), 403

    query_date_raw = request.args.get("date")
    try:
        query_date = _parse_date(query_date_raw, field_name="date") or dt_date.today()
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    asset_payload = {
        "asset_id": request.args.get("asset_id"),
        "machine_code": request.args.get("machine_code"),
    }
    try:
        asset = _get_asset(asset_payload)
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"msg": str(exc)}), 404

    entries = (
        DailyProductionEntry.query.filter_by(date=query_date, asset_id=asset.id)
        .order_by(DailyProductionEntry.hour_no.asc())
        .all()
    )
    entries_by_hour = {entry.hour_no: entry for entry in entries}

    results = []
    total_quantity = 0.0

    for hour in range(1, 25):
        entry = entries_by_hour.get(hour)
        if entry:
            data = entry_schema.dump(entry)
        else:
            data = {
                "id": None,
                "date": query_date.isoformat(),
                "hour_no": hour,
                "quantity_tons": 0.0,
                "asset_id": asset.id,
                "machine_code": asset.code,
                "machine_name": asset.name,
                "updated_at": None,
            }

        quantity_value = data.get("quantity_tons") or 0.0
        try:
            total_quantity += float(quantity_value)
        except (TypeError, ValueError):
            pass
        results.append(data)

    response = {
        "date": query_date.isoformat(),
        "machine": {
            "id": asset.id,
            "code": asset.code,
            "name": asset.name,
        },
        "entries": results,
        "total_quantity_tons": round(total_quantity, 3),
    }

    return jsonify(response)
