"""REST endpoints for manufacturing daily production tracking."""

from datetime import date as dt_date, datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required

from extensions import db
from models import DailyProductionEntry, MachineAsset, RoleEnum
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from schemas import DailyProductionEntrySchema
from sqlalchemy import func


bp = Blueprint("production", __name__, url_prefix="/api/production")

entry_schema = DailyProductionEntrySchema()

SUMMARY_MACHINE_CODES = ("MCH-0001", "MCH-0002")


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


@bp.get("/daily/summary")
@jwt_required()
def get_daily_production_summary():
    if not require_role(
        RoleEnum.production_manager, RoleEnum.admin, RoleEnum.maintenance_manager
    ):
        return jsonify({"msg": "You do not have permission to view production."}), 403

    query_date_raw = request.args.get("date")
    try:
        query_date = _parse_date(query_date_raw, field_name="date") or dt_date.today()
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    machine_codes_param = request.args.get("machine_codes")
    if machine_codes_param:
        raw_codes = [code.strip() for code in machine_codes_param.split(",") if code.strip()]
    else:
        raw_codes = list(SUMMARY_MACHINE_CODES)

    machine_codes = []
    seen_codes = set()
    for code in raw_codes:
        if code and code not in seen_codes:
            machine_codes.append(code)
            seen_codes.add(code)

    if not machine_codes:
        machine_codes = list(SUMMARY_MACHINE_CODES)

    canonical_codes = {}
    for code in machine_codes:
        if code:
            canonical_codes[code.lower()] = code

    assets = (
        MachineAsset.query.filter(MachineAsset.code.in_(machine_codes))
        .order_by(MachineAsset.code.asc())
        .all()
    )
    asset_by_code = {asset.code: asset for asset in assets}

    entries = (
        DailyProductionEntry.query.options(joinedload(DailyProductionEntry.asset))
        .filter_by(date=query_date)
        .all()
    )

    summary_by_hour = {hour: {} for hour in range(1, 25)}

    for entry in entries:
        asset = entry.asset
        if not asset or not asset.code:
            continue
        code = canonical_codes.get(asset.code.lower())
        if not code:
            continue

        quantity = 0.0
        try:
            quantity = float(entry.quantity_tons or 0.0)
        except (TypeError, ValueError):
            quantity = 0.0

        updated_at = getattr(entry, "updated_at", None)
        if updated_at is None:
            updated_at = getattr(entry, "created_at", None)

        if entry.hour_no < 1 or entry.hour_no > 24:
            continue

        summary_by_hour.setdefault(entry.hour_no, {})
        summary_by_hour[entry.hour_no][code] = {
            "entry_id": entry.id,
            "asset_id": asset.id,
            "machine_code": code,
            "quantity_tons": round(quantity, 3),
            "updated_at": updated_at.isoformat() if updated_at else None,
        }

        if code not in asset_by_code:
            asset_by_code[code] = asset

    hours = []
    total_quantity = 0.0

    daily_totals_by_machine = {code: 0.0 for code in machine_codes}

    for hour in range(1, 25):
        hour_data = summary_by_hour.get(hour, {})
        machines_payload = {}
        hour_total = 0.0
        latest_update = None

        for code in machine_codes:
            machine_entry = hour_data.get(code)
            if machine_entry is None:
                asset = asset_by_code.get(code)
                machine_entry = {
                    "entry_id": None,
                    "asset_id": getattr(asset, "id", None),
                    "machine_code": code,
                    "quantity_tons": 0.0,
                    "updated_at": None,
                }
            machines_payload[code] = machine_entry

            try:
                machine_quantity = float(machine_entry.get("quantity_tons") or 0.0)
                hour_total += machine_quantity
                daily_totals_by_machine[code] = daily_totals_by_machine.get(code, 0.0) + machine_quantity
            except (TypeError, ValueError):
                pass

            updated_value = machine_entry.get("updated_at")
            if updated_value:
                try:
                    candidate = datetime.fromisoformat(updated_value)
                except ValueError:
                    candidate = None
                if candidate is not None:
                    if latest_update is None or candidate > latest_update:
                        latest_update = candidate

        total_quantity += hour_total

        hour_payload = {
            "hour_no": hour,
            "machines": machines_payload,
            "hour_total_tons": round(hour_total, 3),
            "last_updated": latest_update.isoformat() if latest_update else None,
        }
        hours.append(hour_payload)

    machines_metadata = []
    for code in machine_codes:
        asset = asset_by_code.get(code)
        machines_metadata.append(
            {
                "code": code,
                "id": getattr(asset, "id", None),
                "name": getattr(asset, "name", code),
            }
        )

    today_totals = {
        "machines": {
            code: round(daily_totals_by_machine.get(code, 0.0), 3) for code in machine_codes
        },
    }
    today_totals["total"] = round(
        sum(today_totals["machines"].values()),
        3,
    )

    month_start = query_date.replace(day=1)
    mtd_totals_by_machine = {code: 0.0 for code in machine_codes}

    mtd_entries = (
        db.session.query(
            MachineAsset.code,
            func.coalesce(func.sum(DailyProductionEntry.quantity_tons), 0),
        )
        .join(DailyProductionEntry, DailyProductionEntry.asset_id == MachineAsset.id)
        .filter(
            DailyProductionEntry.date >= month_start,
            DailyProductionEntry.date <= query_date,
            MachineAsset.code.in_(machine_codes),
        )
        .group_by(MachineAsset.code)
        .all()
    )

    for code_value, total_value in mtd_entries:
        canonical_code = canonical_codes.get(code_value.lower()) if code_value else None
        if canonical_code:
            try:
                mtd_totals_by_machine[canonical_code] = float(total_value or 0.0)
            except (TypeError, ValueError):
                mtd_totals_by_machine[canonical_code] = 0.0

    mtd_totals = {
        "machines": {
            code: round(mtd_totals_by_machine.get(code, 0.0), 3) for code in machine_codes
        },
    }
    mtd_totals["total"] = round(sum(mtd_totals["machines"].values()), 3)

    response = {
        "date": query_date.isoformat(),
        "machines": machines_metadata,
        "hours": hours,
        "total_quantity_tons": round(total_quantity, 3),
        "totals": {
            "today": today_totals,
            "mtd": mtd_totals,
        },
    }

    return jsonify(response)
