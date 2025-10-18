from datetime import date

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
    SalesActualEntry,
    SalesForecastEntry,
)


bp = Blueprint("reports", __name__, url_prefix="/api/reports")


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
