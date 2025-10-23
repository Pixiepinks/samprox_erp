"""REST endpoints for manufacturing daily production tracking."""

import calendar
from datetime import date as dt_date, datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required

from extensions import db
from models import (
    DailyProductionEntry,
    MachineAsset,
    ProductionForecastEntry,
    RoleEnum,
)
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from schemas import DailyProductionEntrySchema, ProductionForecastEntrySchema


bp = Blueprint("production", __name__, url_prefix="/api/production")

entry_schema = DailyProductionEntrySchema()
forecast_entry_schema = ProductionForecastEntrySchema()

SUMMARY_MACHINE_CODES = ("MCH-0001", "MCH-0002")

SRI_LANKA_FALLBACK_HOLIDAYS = {
    2024: [
        (1, 15, "Tamil Thai Pongal Day"),
        (2, 4, "Independence Day"),
        (4, 13, "Sinhala & Tamil New Year Festival"),
        (4, 14, "Sinhala & Tamil New Year"),
        (5, 1, "May Day"),
        (5, 23, "Vesak Full Moon Poya Day"),
        (5, 24, "Day after Vesak Full Moon Poya"),
        (6, 21, "Poson Full Moon Poya Day"),
        (11, 1, "Deepavali Festival Day"),
        (12, 25, "Christmas Day"),
    ],
    2025: [
        (1, 14, "Tamil Thai Pongal Day"),
        (2, 4, "Independence Day"),
        (4, 13, "Sinhala & Tamil New Year Festival"),
        (4, 14, "Sinhala & Tamil New Year"),
        (5, 1, "May Day"),
        (5, 12, "Vesak Full Moon Poya Day"),
        (5, 13, "Day after Vesak Full Moon Poya"),
        (6, 10, "Poson Full Moon Poya Day"),
        (10, 20, "Deepavali Festival Day"),
        (12, 25, "Christmas Day"),
    ],
}


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


def _parse_machine_codes_param(param_value):
    if param_value:
        raw_codes = [code.strip() for code in param_value.split(",") if code.strip()]
    else:
        raw_codes = list(SUMMARY_MACHINE_CODES)

    machine_codes = []
    seen = set()
    for code in raw_codes:
        normalized = (code or "").strip().upper()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        machine_codes.append(normalized)
        seen.add(key)

    if not machine_codes:
        machine_codes = list(SUMMARY_MACHINE_CODES)

    canonical = {code.lower(): code for code in machine_codes}
    filters = list(canonical.keys())

    return machine_codes, canonical, filters


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


def _parse_period_param(period_param):
    if not period_param:
        return dt_date.today().replace(day=1)

    try:
        return datetime.strptime(f"{period_param}-01", "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Invalid period. Use YYYY-MM.") from exc


def _month_range(anchor: dt_date):
    month_days = calendar.monthrange(anchor.year, anchor.month)[1]
    start = anchor
    end = dt_date(anchor.year, anchor.month, month_days)
    return start, end, month_days


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

    machine_codes, canonical_codes, machine_filters = _parse_machine_codes_param(
        request.args.get("machine_codes")
    )

    assets = (
        MachineAsset.query.filter(func.lower(MachineAsset.code).in_(machine_filters))
        .order_by(MachineAsset.code.asc())
        .all()
    )
    asset_by_code = {}
    for asset in assets:
        asset_code = (asset.code or "").lower()
        canonical_code = canonical_codes.get(asset_code)
        if canonical_code:
            asset_by_code[canonical_code] = asset

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
            func.lower(MachineAsset.code).in_(machine_filters),
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


@bp.post("/forecast")
@jwt_required()
def upsert_production_forecast():
    if not require_role(RoleEnum.production_manager, RoleEnum.admin):
        return jsonify({"msg": "You do not have permission to record forecasts."}), 403

    payload = request.get_json() or {}

    try:
        forecast_date = _parse_date(payload.get("date"), field_name="date") or dt_date.today()
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    try:
        asset = _get_asset(payload)
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"msg": str(exc)}), 404

    uses_hours_payload = (
        "forecast_hours" in payload or "average_hourly_production" in payload
    )

    forecast_hours_value = 0.0
    average_hourly_value = 0.0

    if uses_hours_payload:
        try:
            raw_hours = payload.get("forecast_hours", 0)
            forecast_hours_value = float(raw_hours or 0)
        except (TypeError, ValueError):
            return jsonify({"msg": "forecast_hours must be a number."}), 400

        try:
            raw_average = payload.get("average_hourly_production", 0)
            average_hourly_value = float(raw_average or 0)
        except (TypeError, ValueError):
            return jsonify({"msg": "average_hourly_production must be a number."}), 400

        if forecast_hours_value < 0:
            return jsonify({"msg": "forecast_hours cannot be negative."}), 400
        if average_hourly_value < 0:
            return jsonify({"msg": "average_hourly_production cannot be negative."}), 400

        forecast_value = forecast_hours_value * average_hourly_value
    else:
        try:
            forecast_value = float(
                payload.get("forecast_tons", payload.get("quantity_tons", 0))
            )
        except (TypeError, ValueError):
            return jsonify({"msg": "forecast_tons must be a number."}), 400

        if forecast_value < 0:
            return jsonify({"msg": "forecast_tons cannot be negative."}), 400

        forecast_hours_value = 0.0
        average_hourly_value = 0.0

    forecast_value = round(forecast_value, 3)
    forecast_hours_value = round(forecast_hours_value, 3)
    average_hourly_value = round(average_hourly_value, 3)

    entry = ProductionForecastEntry.query.filter_by(
        date=forecast_date,
        asset_id=asset.id,
    ).first()

    status_code = 200
    if entry:
        entry.forecast_tons = forecast_value
        entry.forecast_hours = forecast_hours_value
        entry.average_hourly_production = average_hourly_value
    else:
        entry = ProductionForecastEntry(
            date=forecast_date,
            asset=asset,
            forecast_tons=forecast_value,
            forecast_hours=forecast_hours_value,
            average_hourly_production=average_hourly_value,
        )
        db.session.add(entry)
        status_code = 201

    db.session.commit()

    return jsonify(forecast_entry_schema.dump(entry)), status_code


@bp.get("/forecast")
@jwt_required()
def get_production_forecast():
    if not require_role(
        RoleEnum.production_manager,
        RoleEnum.admin,
        RoleEnum.maintenance_manager,
    ):
        return jsonify({"msg": "You do not have permission to view production."}), 403

    try:
        anchor = _parse_period_param(request.args.get("period"))
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

    month_start, month_end, month_days = _month_range(anchor)

    entries = (
        ProductionForecastEntry.query.filter_by(asset_id=asset.id)
        .filter(
            ProductionForecastEntry.date >= month_start,
            ProductionForecastEntry.date <= month_end,
        )
        .order_by(ProductionForecastEntry.date.asc())
        .all()
    )

    entries_by_date = {entry.date: entry for entry in entries if isinstance(entry.date, dt_date)}

    forecast_entries = []
    total_forecast = 0.0

    for day in range(1, month_days + 1):
        current_date = dt_date(anchor.year, anchor.month, day)
        entry = entries_by_date.get(current_date)

        forecast_value = 0.0
        forecast_hours_value = 0.0
        average_hourly_value = 0.0
        entry_id = None
        updated_at_value = None

        if entry:
            entry_id = entry.id
            updated_at_value = entry.updated_at
            try:
                forecast_value = float(entry.forecast_tons or 0.0)
            except (TypeError, ValueError):
                forecast_value = 0.0
            try:
                forecast_hours_value = float(entry.forecast_hours or 0.0)
            except (TypeError, ValueError):
                forecast_hours_value = 0.0
            try:
                average_hourly_value = float(
                    entry.average_hourly_production or 0.0
                )
            except (TypeError, ValueError):
                average_hourly_value = 0.0

        forecast_value = round(forecast_value, 3)
        forecast_hours_value = round(forecast_hours_value, 3)
        average_hourly_value = round(average_hourly_value, 3)
        total_forecast += forecast_value

        forecast_entries.append(
            {
                "day": day,
                "date": current_date.isoformat(),
                "forecast_tons": forecast_value,
                "forecast_hours": forecast_hours_value,
                "average_hourly_production": average_hourly_value,
                "entry_id": entry_id,
                "updated_at": updated_at_value.isoformat() if updated_at_value else None,
            }
        )

    total_forecast = round(total_forecast, 3)

    response = {
        "period": anchor.strftime("%Y-%m"),
        "label": anchor.strftime("%B %Y"),
        "machine": {
            "id": asset.id,
            "code": asset.code,
            "name": asset.name,
        },
        "start_date": month_start.isoformat(),
        "end_date": month_end.isoformat(),
        "days": month_days,
        "entries": forecast_entries,
        "total_forecast_tons": total_forecast,
    }

    return jsonify(response)


@bp.get("/forecast/holidays")
@jwt_required()
def get_production_forecast_holidays():
    if not require_role(
        RoleEnum.production_manager,
        RoleEnum.admin,
        RoleEnum.maintenance_manager,
    ):
        return jsonify({"msg": "You do not have permission to view production."}), 403

    year_param = request.args.get("year")
    if year_param:
        try:
            year = int(year_param)
        except (TypeError, ValueError):
            return jsonify({"msg": "Invalid year. Use a four digit year."}), 400
    else:
        year = dt_date.today().year

    if year < 1900 or year > 2100:
        return jsonify({"msg": "Year must be between 1900 and 2100."}), 400

    fallback = SRI_LANKA_FALLBACK_HOLIDAYS.get(year, [])
    holidays_payload = []

    for month, day, name in fallback:
        try:
            holiday_date = dt_date(year, month, day)
        except ValueError:
            continue
        holidays_payload.append({"date": holiday_date.isoformat(), "name": name})

    holidays_payload.sort(key=lambda item: item["date"])

    return jsonify({"year": year, "holidays": holidays_payload})


@bp.get("/monthly/summary")
@jwt_required()
def get_monthly_production_summary():
    if not require_role(
        RoleEnum.production_manager, RoleEnum.admin, RoleEnum.maintenance_manager
    ):
        return jsonify({"msg": "You do not have permission to view production."}), 403

    try:
        anchor = _parse_period_param(request.args.get("period"))
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    machine_codes, canonical_codes, machine_filters = _parse_machine_codes_param(
        request.args.get("machine_codes")
    )

    month_start, month_end, month_days = _month_range(anchor)

    totals_query = (
        db.session.query(
            DailyProductionEntry.date,
            func.lower(MachineAsset.code),
            func.coalesce(func.sum(DailyProductionEntry.quantity_tons), 0.0),
        )
        .join(MachineAsset, DailyProductionEntry.asset_id == MachineAsset.id)
        .filter(
            DailyProductionEntry.date >= month_start,
            DailyProductionEntry.date <= month_end,
            func.lower(MachineAsset.code).in_(machine_filters),
        )
        .group_by(DailyProductionEntry.date, func.lower(MachineAsset.code))
        .order_by(DailyProductionEntry.date.asc())
    )

    totals_by_date = {}
    for date_value, code_value, total_value in totals_query.all():
        if not isinstance(date_value, dt_date):
            continue
        day_totals = totals_by_date.setdefault(date_value, {})
        try:
            day_totals[code_value] = float(total_value or 0.0)
        except (TypeError, ValueError):
            day_totals[code_value] = 0.0

    daily_totals = []
    total_production = 0.0

    machine_field_map = {
        code: code.replace("MCH-000", "MCH") if code.startswith("MCH-000") else code.replace("-", "")
        for code in machine_codes
    }

    for day in range(1, month_days + 1):
        current_date = dt_date(anchor.year, anchor.month, day)
        machine_values = totals_by_date.get(current_date, {})

        payload = {
            "day": day,
            "date": current_date.isoformat(),
        }

        day_total = 0.0
        for machine_filter, canonical_code in canonical_codes.items():
            value = round(machine_values.get(machine_filter, 0.0), 3)
            field_name = machine_field_map.get(canonical_code, canonical_code)
            payload[field_name] = value
            day_total += value

        day_total = round(day_total, 3)
        payload["total_tons"] = day_total
        total_production += day_total
        daily_totals.append(payload)

    total_production = round(total_production, 3)
    average_day_production = round(
        total_production / month_days if month_days else 0.0,
        3,
    )
    peak_day_payload = max(daily_totals, key=lambda item: item["total_tons"], default=None)
    if peak_day_payload:
        peak = {
            "day": peak_day_payload["day"],
            "total_tons": peak_day_payload["total_tons"],
        }
    else:
        peak = {"day": None, "total_tons": 0.0}

    response = {
        "period": anchor.strftime("%Y-%m"),
        "label": anchor.strftime("%B %Y"),
        "start_date": month_start.isoformat(),
        "end_date": month_end.isoformat(),
        "days": month_days,
        "machine_codes": machine_codes,
        "daily_totals": daily_totals,
        "total_production": total_production,
        "average_day_production": average_day_production,
        "peak": peak,
    }

    return jsonify(response)
