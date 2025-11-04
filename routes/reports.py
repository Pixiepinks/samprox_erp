from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import func

from extensions import db
from models import (
    Customer,
    Job,
    JobStatus,
    LaborEntry,
    MaterialEntry,
    MaterialItem,
    MRNHeader,
    MRNLine,
    SalesActualEntry,
    SalesForecastEntry,
    PayCategory,
    TeamAttendanceRecord,
    TeamMember,
    TeamMemberStatus,
    TeamSalaryRecord,
)

from material.services import DEFAULT_MATERIAL_ITEM_NAMES
from routes.team import (
    _build_work_calendar_lookup,
    _calculate_entry_overtime_minutes,
    _decimal_from_value,
    _get_month_bounds,
    _resolve_pay_category,
)


bp = Blueprint("reports", __name__, url_prefix="/api/reports")


DEFAULT_MATERIAL_FIELD_CONFIG = [
    (name, f"material_{name.lower().replace(' ', '_')}")
    for name in DEFAULT_MATERIAL_ITEM_NAMES
]
DEFAULT_MATERIAL_LOOKUP = {
    name.lower(): (name, field) for name, field in DEFAULT_MATERIAL_FIELD_CONFIG
}
OTHER_MATERIAL_LABEL = "Other materials"
OTHER_MATERIAL_FIELD = "material_other"


def _quantize_currency(value: Decimal | None) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal("0")
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _normalize_period_param(period: str | None) -> str | None:
    if not period:
        return None
    try:
        anchor = datetime.strptime(f"{period}-01", "%Y-%m-%d")
    except ValueError:
        return None
    return anchor.strftime("%Y-%m")


def _did_casual_work(entry: dict | None) -> bool:
    if not isinstance(entry, dict):
        return False

    status_value = entry.get("dayStatus")
    if isinstance(status_value, str) and status_value.strip().lower().startswith("work"):
        return True

    on_value = entry.get("onTime")
    if isinstance(on_value, str) and on_value.strip():
        return True

    off_value = entry.get("offTime")
    if isinstance(off_value, str) and off_value.strip():
        return True

    return False


@bp.get("/costs")
@jwt_required()
def job_costs():
    job_id = int(request.args["job_id"])
    labor = (
        db.session.query(
            func.coalesce(func.sum(LaborEntry.hours * LaborEntry.rate), 0)
        )
        .filter_by(job_id=job_id)
        .scalar()
    )
    materials = (
        db.session.query(
            func.coalesce(func.sum(MaterialEntry.qty * MaterialEntry.unit_cost), 0)
        )
        .filter_by(job_id=job_id)
        .scalar()
    )
    return jsonify(
        {
            "job_id": job_id,
            "labor_cost": float(labor),
            "material_cost": float(materials),
            "total_cost": float(labor + materials),
        }
    )


@bp.get("/customer-sales")
@jwt_required()
def customer_sales_report():
    try:
        year = int(request.args["year"])
        month = int(request.args["month"])
        if month < 1 or month > 12:
            raise ValueError
    except (KeyError, ValueError):
        return jsonify({"msg": "year and month query params are required"}), 400

    customer_id = request.args.get("customer_id", type=int)

    start_date = date(year, month, 1)
    if month == 12:
        end_date = date(year + 1, 1, 1)
    else:
        end_date = date(year, month + 1, 1)

    customer_query = Customer.query
    if customer_id:
        customer_query = customer_query.filter_by(id=customer_id)

    customers = customer_query.order_by(Customer.name.asc()).all()
    if not customers:
        return jsonify({"year": year, "month": month, "customers": []})

    customer_ids = [customer.id for customer in customers]
    def _empty_day():
        return {
            "forecast_amount": 0.0,
            "actual_amount": 0.0,
            "forecast_quantity_tons": 0.0,
            "actual_quantity_tons": 0.0,
        }

    summary = {customer.id: {"customer": customer, "dates": {}} for customer in customers}

    forecast_rows = (
        db.session.query(
            SalesForecastEntry.customer_id,
            SalesForecastEntry.date,
            func.sum(SalesForecastEntry.amount).label("amount"),
            func.sum(SalesForecastEntry.quantity_tons).label("quantity"),
        )
        .filter(SalesForecastEntry.date >= start_date, SalesForecastEntry.date < end_date)
        .filter(SalesForecastEntry.customer_id.in_(customer_ids))
        .group_by(SalesForecastEntry.customer_id, SalesForecastEntry.date)
        .all()
    )

    for row in forecast_rows:
        bucket = summary.get(row.customer_id)
        if not bucket:
            continue
        bucket["dates"].setdefault(row.date, _empty_day())
        bucket["dates"][row.date]["forecast_amount"] = float(row.amount or 0.0)
        bucket["dates"][row.date]["forecast_quantity_tons"] = float(row.quantity or 0.0)

    actual_rows = (
        db.session.query(
            SalesActualEntry.customer_id,
            SalesActualEntry.date,
            func.sum(SalesActualEntry.amount).label("amount"),
            func.sum(SalesActualEntry.quantity_tons).label("quantity"),
        )
        .filter(SalesActualEntry.date >= start_date, SalesActualEntry.date < end_date)
        .filter(SalesActualEntry.customer_id.in_(customer_ids))
        .group_by(SalesActualEntry.customer_id, SalesActualEntry.date)
        .all()
    )

    for row in actual_rows:
        bucket = summary.get(row.customer_id)
        if not bucket:
            continue
        bucket["dates"].setdefault(row.date, _empty_day())
        bucket["dates"][row.date]["actual_amount"] = float(row.amount or 0.0)
        bucket["dates"][row.date]["actual_quantity_tons"] = float(row.quantity or 0.0)

    payload = []
    for customer_id_value, bucket in summary.items():
        dates = []
        monthly_forecast_total = 0.0
        monthly_actual_total = 0.0
        monthly_forecast_quantity = 0.0
        monthly_actual_quantity = 0.0
        for day in sorted(bucket["dates"].keys()):
            entry = bucket["dates"][day]
            monthly_forecast_total += entry["forecast_amount"]
            monthly_actual_total += entry["actual_amount"]
            monthly_forecast_quantity += entry["forecast_quantity_tons"]
            monthly_actual_quantity += entry["actual_quantity_tons"]
            dates.append(
                {
                    "date": day.isoformat(),
                    "forecast_amount": entry["forecast_amount"],
                    "actual_amount": entry["actual_amount"],
                    "forecast_quantity_tons": entry["forecast_quantity_tons"],
                    "actual_quantity_tons": entry["actual_quantity_tons"],
                }
            )

        if monthly_actual_quantity:
            monthly_average_unit_price = monthly_actual_total / monthly_actual_quantity
        else:
            monthly_average_unit_price = 0.0

        payload.append(
            {
                "customer_id": customer_id_value,
                "customer_name": bucket["customer"].name,
                "customer_category": bucket["customer"].category.value,
                "dates": dates,
                "monthly_forecast_total": monthly_forecast_total,
                "monthly_actual_total": monthly_actual_total,
                "monthly_forecast_quantity_tons": monthly_forecast_quantity,
                "monthly_actual_quantity_tons": monthly_actual_quantity,
                "monthly_average_unit_price": monthly_average_unit_price,
                "monthly_total_sales_amount": monthly_actual_total,
            }
        )

    return jsonify({"year": year, "month": month, "customers": payload})


@bp.get("/sales-summary")
@jwt_required()
def sales_summary():
    today = date.today()
    as_of_param = request.args.get("as_of")

    if as_of_param:
        try:
            today = date.fromisoformat(as_of_param)
        except ValueError:
            return jsonify({"msg": "Invalid as_of date. Use YYYY-MM-DD."}), 400

    start_of_year = date(today.year, 1, 1)
    start_of_month = date(today.year, today.month, 1)
    days_in_month = monthrange(today.year, today.month)[1]

    entries = (
        db.session.query(
            SalesActualEntry.date,
            SalesActualEntry.amount,
            SalesActualEntry.quantity_tons,
            SalesActualEntry.customer_id,
        )
        .filter(SalesActualEntry.date >= start_of_year, SalesActualEntry.date <= today)
        .all()
    )

    monthly_values = [0.0 for _ in range(12)]
    monthly_quantities = [0.0 for _ in range(12)]
    daily_values = [0.0 for _ in range(days_in_month)]

    year_value = 0.0
    year_quantity = 0.0
    month_value = 0.0
    month_quantity = 0.0
    customer_totals = {}

    for entry in entries:
        if not entry.date:
            continue

        amount = float(entry.amount or 0.0)
        quantity = float(entry.quantity_tons or 0.0)
        month_index = entry.date.month - 1
        if month_index < 0 or month_index >= 12:
            continue

        monthly_values[month_index] += amount
        monthly_quantities[month_index] += quantity
        year_value += amount
        year_quantity += quantity

        if entry.customer_id is not None:
            bucket = customer_totals.setdefault(
                entry.customer_id, {"value": 0.0, "quantity": 0.0}
            )
            bucket["value"] += amount
            bucket["quantity"] += quantity

        if entry.date >= start_of_month:
            day_index = entry.date.day - 1
            if 0 <= day_index < days_in_month:
                month_value += amount
                month_quantity += quantity
                daily_values[day_index] += amount

    average_unit_price = month_value / month_quantity if month_quantity else 0.0

    top_customer = None
    if customer_totals:
        top_customer_id, totals = max(
            customer_totals.items(), key=lambda item: (item[1]["value"], item[1]["quantity"])
        )
        customer = Customer.query.get(top_customer_id)
        top_customer = {
            "id": top_customer_id,
            "name": customer.name if customer else str(top_customer_id),
            "quantity_tons": round(totals["quantity"], 2),
            "sales_value": round(totals["value"], 2),
        }

    def _round_list(values):
        return [round(value, 2) for value in values]

    payload = {
        "year": today.year,
        "month": today.month,
        "as_of": today.isoformat(),
        "year_to_date": {
            "sales_value": round(year_value, 2),
            "quantity_tons": round(year_quantity, 2),
            "monthly_values": _round_list(monthly_values),
            "monthly_quantities": _round_list(monthly_quantities),
        },
        "month_to_date": {
            "sales_value": round(month_value, 2),
            "quantity_tons": round(month_quantity, 2),
            "daily_values": _round_list(daily_values),
        },
        "monthly_average_unit_price": round(average_unit_price, 2),
        "top_customer": top_customer,
    }

    return jsonify(payload)


@bp.get("/sales/monthly-summary")
@jwt_required()
def monthly_sales_summary():
    period_param = request.args.get("period")
    if period_param:
        try:
            anchor = datetime.strptime(f"{period_param}-01", "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"msg": "Invalid period. Use YYYY-MM."}), 400
    else:
        anchor = date.today().replace(day=1)

    days_in_month = monthrange(anchor.year, anchor.month)[1]
    month_start = date(anchor.year, anchor.month, 1)
    month_end = date(anchor.year, anchor.month, days_in_month)

    totals_query = (
        db.session.query(
            SalesActualEntry.date,
            SalesActualEntry.customer_id,
            func.coalesce(func.sum(SalesActualEntry.amount), 0.0),
            func.coalesce(func.sum(SalesActualEntry.quantity_tons), 0.0),
        )
        .filter(
            SalesActualEntry.date >= month_start,
            SalesActualEntry.date <= month_end,
        )
        .group_by(SalesActualEntry.date, SalesActualEntry.customer_id)
        .order_by(SalesActualEntry.date.asc())
    )

    totals_by_date = {}
    quantity_by_date = {}
    customer_totals = {}
    customer_quantities = {}

    for entry_date, customer_id, total_amount, total_quantity in totals_query.all():
        if not isinstance(entry_date, date):
            continue

        amount_value = float(total_amount or 0.0)
        quantity_value = float(total_quantity or 0.0)
        day_totals = totals_by_date.setdefault(entry_date, {})
        day_totals[customer_id] = day_totals.get(customer_id, 0.0) + amount_value
        day_quantities = quantity_by_date.setdefault(entry_date, {})
        day_quantities[customer_id] = day_quantities.get(customer_id, 0.0) + quantity_value
        customer_totals[customer_id] = customer_totals.get(customer_id, 0.0) + amount_value
        customer_quantities[customer_id] = (
            customer_quantities.get(customer_id, 0.0) + quantity_value
        )

    customer_ids = [customer_id for customer_id in customer_totals.keys() if customer_id is not None]
    customer_names = {}
    if customer_ids:
        for customer_id, name in (
            db.session.query(Customer.id, Customer.name)
            .filter(Customer.id.in_(customer_ids))
            .all()
        ):
            customer_names[customer_id] = name

    customer_names[None] = "Unassigned sales"

    sorted_totals = sorted(
        customer_totals.items(), key=lambda item: (item[1] or 0.0), reverse=True
    )

    max_customers = 5
    customer_metadata = []
    selected_ids = set()

    for customer_id, total in sorted_totals[:max_customers]:
        field_name = "customer_unassigned" if customer_id is None else f"customer_{customer_id}"
        selected_ids.add(customer_id)
        customer_metadata.append(
            {
                "id": customer_id,
                "name": customer_names.get(customer_id)
                or ("Customer" if customer_id is None else f"Customer {customer_id}"),
                "field": field_name,
                "total_sales": round(float(total or 0.0), 2),
                "is_other": False,
            }
        )

    include_other_bucket = len(sorted_totals) > len(customer_metadata)
    other_total_value = 0.0
    if include_other_bucket:
        for customer_id, total in sorted_totals:
            if customer_id not in selected_ids:
                other_total_value += float(total or 0.0)

        customer_metadata.append(
            {
                "id": "other",
                "name": "Other customers",
                "field": "customer_other",
                "total_sales": round(other_total_value, 2),
                "is_other": True,
            }
        )

    daily_totals = []
    total_sales_value = 0.0
    total_quantity_tons = 0.0

    non_other_ids = {item["id"] for item in customer_metadata if not item.get("is_other")}

    for day in range(1, days_in_month + 1):
        current_date = date(anchor.year, anchor.month, day)
        day_totals = totals_by_date.get(current_date, {})
        day_quantities = quantity_by_date.get(current_date, {})
        payload = {"day": day, "date": current_date.isoformat()}

        day_total_value = 0.0
        day_total_quantity = 0.0
        for metadata in customer_metadata:
            if metadata.get("is_other"):
                value = sum(
                    float(amount or 0.0)
                    for customer_id, amount in day_totals.items()
                    if customer_id not in non_other_ids
                )
                quantity = sum(
                    float(quantity or 0.0)
                    for customer_id, quantity in day_quantities.items()
                    if customer_id not in non_other_ids
                )
            else:
                value = float(day_totals.get(metadata["id"], 0.0))
                quantity = float(day_quantities.get(metadata["id"], 0.0))

            value = round(value, 2)
            quantity = round(quantity, 2)
            payload[metadata["field"]] = value
            day_total_value += value
            day_total_quantity += quantity

        day_total_value = round(day_total_value, 2)
        day_total_quantity = round(day_total_quantity, 2)
        payload["total_value"] = day_total_value
        payload["total_quantity_tons"] = day_total_quantity
        total_sales_value += day_total_value
        total_quantity_tons += day_total_quantity
        daily_totals.append(payload)

    total_sales_value = round(total_sales_value, 2)
    total_quantity_tons = round(total_quantity_tons, 2)
    average_day_sales = round(total_sales_value / days_in_month if days_in_month else 0.0, 2)
    average_day_quantity = round(
        total_quantity_tons / days_in_month if days_in_month else 0.0, 2
    )

    peak_day_payload = max(daily_totals, key=lambda item: item["total_value"], default=None)
    if peak_day_payload:
        peak = {
            "day": peak_day_payload["day"],
            "total_value": peak_day_payload["total_value"],
            "total_quantity_tons": peak_day_payload.get("total_quantity_tons", 0.0),
        }
    else:
        peak = {"day": None, "total_value": 0.0, "total_quantity_tons": 0.0}

    top_customer = None
    if sorted_totals:
        top_customer_id, top_customer_total = sorted_totals[0]
        top_customer = {
            "id": top_customer_id,
            "name": customer_names.get(top_customer_id)
            or ("Customer" if top_customer_id is None else f"Customer {top_customer_id}"),
            "sales_value": round(float(top_customer_total or 0.0), 2),
            "quantity_tons": round(float(customer_quantities.get(top_customer_id, 0.0)), 2),
        }

    response = {
        "period": anchor.strftime("%Y-%m"),
        "label": anchor.strftime("%B %Y"),
        "start_date": month_start.isoformat(),
        "end_date": month_end.isoformat(),
        "days": days_in_month,
        "customers": customer_metadata,
        "daily_totals": daily_totals,
        "total_sales": total_sales_value,
        "average_day_sales": average_day_sales,
        "total_quantity_tons": total_quantity_tons,
        "average_day_quantity_tons": average_day_quantity,
        "peak": peak,
        "top_customer": top_customer,
    }

    return jsonify(response)


@bp.get("/labor/daily-production-cost")
@jwt_required()
def labor_daily_production_cost():
    period_param = _normalize_period_param(request.args.get("period"))
    if not period_param:
        today = date.today()
        period_param = today.strftime("%Y-%m")

    bounds = _get_month_bounds(period_param)
    if not bounds:
        return jsonify({"msg": "Invalid period. Use YYYY-MM."}), 400

    month_start, month_end, days_in_month = bounds

    today = date.today()
    if month_start <= today <= month_end:
        active_end = today
    else:
        active_end = month_end

    active_day_count = max((active_end - month_start).days + 1, 0)

    members_query = TeamMember.query.filter(
        TeamMember.pay_category.in_(
            (PayCategory.FACTORY.value, PayCategory.CASUAL.value)
        )
    ).filter(TeamMember.status == TeamMemberStatus.ACTIVE.value)

    members = (
        members_query.order_by(TeamMember.pay_category.asc(), TeamMember.name.asc())
        .all()
    )

    member_ids = [member.id for member in members if member.id is not None]

    salary_records = []
    attendance_records = []
    if member_ids:
        salary_records = (
            TeamSalaryRecord.query.filter_by(month=period_param)
            .filter(TeamSalaryRecord.team_member_id.in_(member_ids))
            .all()
        )
        attendance_records = (
            TeamAttendanceRecord.query.filter_by(month=period_param)
            .filter(TeamAttendanceRecord.team_member_id.in_(member_ids))
            .all()
        )

    salary_lookup = {
        record.team_member_id: record.components
        if isinstance(record.components, dict)
        else {}
        for record in salary_records
    }

    attendance_lookup = {
        record.team_member_id: record.entries
        if isinstance(record.entries, dict)
        else {}
        for record in attendance_records
    }

    work_calendar_lookup = _build_work_calendar_lookup(period_param)

    month_work_day_count = 0
    current_month_day = month_start
    while current_month_day <= month_end:
        month_iso = current_month_day.isoformat()
        is_work_day = work_calendar_lookup.get(month_iso)
        if is_work_day is not False:
            month_work_day_count += 1
        current_month_day += timedelta(days=1)

    work_day_flags = []
    for offset in range(active_day_count):
        current = month_start + timedelta(days=offset)
        iso = current.isoformat()
        is_work_day = work_calendar_lookup.get(iso)
        work_day_flags.append((current, is_work_day is not False))

    work_day_count = sum(1 for _, flag in work_day_flags if flag)

    member_profiles = {}
    for member in members:
        if member.id is None:
            continue
        pay_category = _resolve_pay_category(member)
        components = salary_lookup.get(member.id, {})

        profile: dict[str, Decimal | PayCategory | None] = {
            "pay_category": pay_category,
        }

        if pay_category == PayCategory.FACTORY:
            basic = _decimal_from_value(components.get("basicSalary"))
            attendance_allowance = _decimal_from_value(
                components.get("attendanceAllowance")
            )
            target_allowance = _decimal_from_value(components.get("targetAllowance"))
            base_total = basic + attendance_allowance + target_allowance
            if month_work_day_count > 0:
                profile["base_daily"] = base_total / Decimal(month_work_day_count)
            else:
                profile["base_daily"] = Decimal("0")

            if basic > 0:
                profile["ot_rate"] = (basic / Decimal("200")) * Decimal("1.5")
            else:
                profile["ot_rate"] = Decimal("0")
        elif pay_category == PayCategory.CASUAL:
            day_salary = _decimal_from_value(components.get("daySalary"))
            profile["day_salary"] = day_salary
            profile["ot_rate"] = _decimal_from_value(components.get("casualOtRate"))
        else:
            profile["base_daily"] = Decimal("0")
            profile["day_salary"] = Decimal("0")
            profile["ot_rate"] = Decimal("0")

        member_profiles[member.id] = profile

    daily_results = []
    member_totals = {
        member.id: Decimal("0") for member in members if member.id is not None
    }

    for current_date, is_work_day in work_day_flags:
        iso = current_date.isoformat()
        day_number = current_date.day
        member_costs_payload: dict[str, float] = {}
        day_total = Decimal("0")

        for member in members:
            if member.id is None:
                continue

            profile = member_profiles.get(member.id)
            if not profile:
                continue

            pay_category = profile.get("pay_category")
            amount = Decimal("0")

            if pay_category == PayCategory.FACTORY:
                base_daily = profile.get("base_daily") or Decimal("0")
                if is_work_day:
                    amount += base_daily

            elif pay_category == PayCategory.CASUAL:
                entries = attendance_lookup.get(member.id, {})
                entry = entries.get(iso, {})
                if _did_casual_work(entry):
                    day_salary = profile.get("day_salary") or Decimal("0")
                    amount += day_salary

            entries = attendance_lookup.get(member.id, {})
            entry = entries.get(iso, {})
            overtime_minutes = _calculate_entry_overtime_minutes(
                iso, entry, work_calendar_lookup, pay_category
            )
            ot_rate = profile.get("ot_rate") or Decimal("0")
            if overtime_minutes and ot_rate > 0:
                ot_hours = Decimal(overtime_minutes) / Decimal(60)
                amount += ot_hours * ot_rate

            if amount <= 0:
                continue

            quantized_amount = _quantize_currency(amount)
            member_costs_payload[str(member.id)] = float(quantized_amount)
            day_total += quantized_amount
            member_totals[member.id] = member_totals.get(member.id, Decimal("0")) + quantized_amount

        day_total = _quantize_currency(day_total)
        daily_results.append(
            {
                "day": day_number,
                "date": iso,
                "is_work_day": is_work_day,
                "member_costs": member_costs_payload,
                "total_cost": float(day_total),
                "_total_decimal": day_total,
            }
        )

    monthly_total = sum((entry["_total_decimal"] for entry in daily_results), Decimal("0"))
    monthly_total = _quantize_currency(monthly_total)

    if work_day_count > 0:
        average_work_day_cost = _quantize_currency(monthly_total / Decimal(work_day_count))
    else:
        average_work_day_cost = Decimal("0.00")

    peak_entry = max(daily_results, key=lambda entry: entry["_total_decimal"], default=None)
    peak_payload = {
        "day": peak_entry["day"] if peak_entry else None,
        "date": peak_entry["date"] if peak_entry else None,
        "total_cost": float(_quantize_currency(peak_entry["_total_decimal"]))
        if peak_entry
        else 0.0,
    }

    top_member_id = None
    top_member_total = Decimal("0")
    for member_id, total in member_totals.items():
        if total > top_member_total:
            top_member_total = total
            top_member_id = member_id

    top_member_payload = None
    if top_member_id is not None:
        top_member = next((m for m in members if m.id == top_member_id), None)
        if top_member:
            pay_category = _resolve_pay_category(top_member)
            top_member_payload = {
                "id": top_member.id,
                "name": top_member.name,
                "regNumber": top_member.reg_number,
                "payCategory": pay_category.value if isinstance(pay_category, PayCategory) else None,
                "total_cost": float(_quantize_currency(top_member_total)),
            }

    members_payload = []
    for member in members:
        pay_category = _resolve_pay_category(member)
        members_payload.append(
            {
                "id": member.id,
                "name": member.name,
                "regNumber": member.reg_number,
                "payCategory": pay_category.value if isinstance(pay_category, PayCategory) else None,
            }
        )

    for entry in daily_results:
        entry.pop("_total_decimal", None)

    return jsonify(
        {
            "period": period_param,
            "label": month_start.strftime("%B %Y"),
            "start_date": month_start.isoformat(),
            "end_date": active_end.isoformat(),
            "days": active_day_count,
            "work_day_count": work_day_count,
            "monthly_total": float(monthly_total),
            "average_work_day_cost": float(average_work_day_cost),
            "peak_day": peak_payload,
            "top_member": top_member_payload,
            "members": members_payload,
            "daily_totals": daily_results,
        }
    )


@bp.get("/materials/monthly-summary")
@jwt_required()
def monthly_material_summary():
    period_param = request.args.get("period")
    if period_param:
        try:
            anchor = datetime.strptime(f"{period_param}-01", "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"msg": "Invalid period. Use YYYY-MM."}), 400
    else:
        anchor = date.today().replace(day=1)

    days_in_month = monthrange(anchor.year, anchor.month)[1]
    month_start = date(anchor.year, anchor.month, 1)
    month_end = date(anchor.year, anchor.month, days_in_month)

    totals_query = (
        db.session.query(
            MRNHeader.date,
            MaterialItem.name,
            func.coalesce(func.sum(MRNLine.qty_ton), 0.0),
        )
        .join(MRNLine, MRNLine.mrn_id == MRNHeader.id)
        .join(MaterialItem, MRNLine.item_id == MaterialItem.id)
        .filter(MRNHeader.date >= month_start, MRNHeader.date <= month_end)
        .group_by(MRNHeader.date, MaterialItem.name)
        .order_by(MRNHeader.date.asc())
    )

    material_fields = [field for _, field in DEFAULT_MATERIAL_FIELD_CONFIG]
    field_labels = {field: name for name, field in DEFAULT_MATERIAL_FIELD_CONFIG}
    material_totals = {field: 0.0 for field in material_fields}

    daily_totals = []
    day_lookup = {}
    for day in range(1, days_in_month + 1):
        current_date = date(anchor.year, anchor.month, day)
        payload = {
            "day": day,
            "date": current_date.isoformat(),
            "total_quantity_tons": 0.0,
        }
        for field in material_fields:
            payload[field] = 0.0
        daily_totals.append(payload)
        day_lookup[current_date] = payload

    def ensure_other_field() -> None:
        if OTHER_MATERIAL_FIELD in material_totals:
            return
        material_totals[OTHER_MATERIAL_FIELD] = 0.0
        field_labels[OTHER_MATERIAL_FIELD] = OTHER_MATERIAL_LABEL
        material_fields.append(OTHER_MATERIAL_FIELD)
        for entry in daily_totals:
            entry[OTHER_MATERIAL_FIELD] = 0.0

    for entry_date, item_name, total_qty in totals_query.all():
        if isinstance(entry_date, datetime):
            entry_date = entry_date.date()
        if not isinstance(entry_date, date):
            continue

        day_payload = day_lookup.get(entry_date)
        if not day_payload:
            continue

        normalized_name = (item_name or "").strip().lower()
        if normalized_name in DEFAULT_MATERIAL_LOOKUP:
            label, field = DEFAULT_MATERIAL_LOOKUP[normalized_name]
        else:
            ensure_other_field()
            label = OTHER_MATERIAL_LABEL
            field = OTHER_MATERIAL_FIELD

        value = float(total_qty or 0.0)
        day_payload[field] = day_payload.get(field, 0.0) + value
        day_payload["total_quantity_tons"] += value
        material_totals[field] = material_totals.get(field, 0.0) + value
        field_labels[field] = label

    total_quantity = sum(material_totals.values())
    average_day_quantity = (
        total_quantity / days_in_month if days_in_month else 0.0
    )

    peak_day_entry = max(
        daily_totals,
        key=lambda entry: entry["total_quantity_tons"],
        default=None,
    )
    if peak_day_entry and peak_day_entry["total_quantity_tons"] > 0:
        peak = {
            "day": peak_day_entry["day"],
            "total_quantity_tons": round(peak_day_entry["total_quantity_tons"], 2),
        }
    else:
        peak = {"day": None, "total_quantity_tons": 0.0}

    top_material_field = None
    top_material_total = 0.0
    if material_totals:
        top_material_field, top_material_total = max(
            material_totals.items(), key=lambda item: item[1]
        )

    materials_metadata = []
    for name, field in DEFAULT_MATERIAL_FIELD_CONFIG:
        materials_metadata.append(
            {
                "name": name,
                "field": field,
                "total_quantity_tons": round(material_totals.get(field, 0.0), 2),
            }
        )

    if (
        OTHER_MATERIAL_FIELD in material_totals
        and material_totals[OTHER_MATERIAL_FIELD] > 0
    ):
        materials_metadata.append(
            {
                "name": OTHER_MATERIAL_LABEL,
                "field": OTHER_MATERIAL_FIELD,
                "total_quantity_tons": round(material_totals[OTHER_MATERIAL_FIELD], 2),
            }
        )

    for entry in daily_totals:
        for field in material_fields:
            entry[field] = round(entry.get(field, 0.0), 2)
        entry["total_quantity_tons"] = round(entry["total_quantity_tons"], 2)

    response = {
        "period": anchor.strftime("%Y-%m"),
        "label": anchor.strftime("%B %Y"),
        "start_date": month_start.isoformat(),
        "end_date": month_end.isoformat(),
        "days": days_in_month,
        "materials": materials_metadata,
        "daily_totals": daily_totals,
        "total_quantity_tons": round(total_quantity, 2),
        "average_day_quantity_tons": round(average_day_quantity, 2),
        "peak": peak,
        "top_material": (
            {
                "name": field_labels.get(top_material_field, ""),
                "quantity_tons": round(top_material_total, 2),
            }
            if top_material_field and top_material_total > 0
            else None
        ),
    }

    return jsonify(response)
