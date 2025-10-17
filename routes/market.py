from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import func

from extensions import db
from models import Customer, SalesActualEntry, SalesForecastEntry

bp = Blueprint("market", __name__, url_prefix="/api/market")


@bp.get("/customers")
@jwt_required()
def list_customers():
    customers = Customer.query.order_by(Customer.name.asc()).all()
    return jsonify(
        {
            "customers": [
                {"id": customer.id, "name": customer.name}
                for customer in customers
            ]
        }
    )


@bp.post("/customers")
@jwt_required()
def create_customer():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()

    if not name:
        return jsonify({"msg": "Customer name is required"}), 400

    existing = Customer.query.filter(func.lower(Customer.name) == name.lower()).first()
    if existing:
        return (
            jsonify({"msg": "A customer with this name already exists.", "customer": {"id": existing.id, "name": existing.name}}),
            409,
        )

    customer = Customer(name=name)
    db.session.add(customer)
    db.session.commit()

    return jsonify({"customer": {"id": customer.id, "name": customer.name}}), 201


@bp.post("/sales")
@jwt_required()
def record_sale_entry():
    payload = request.get_json(silent=True) or {}

    try:
        customer_id = int(payload.get("customer_id"))
    except (TypeError, ValueError):
        return jsonify({"msg": "A valid customer_id is required"}), 400

    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"msg": "Customer not found"}), 404

    sale_type = (payload.get("sale_type") or "").strip().lower()
    if sale_type not in {"actual", "forecast"}:
        return jsonify({"msg": "sale_type must be either 'actual' or 'forecast'"}), 400

    date_str = payload.get("date")
    try:
        entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return jsonify({"msg": "date must be provided in YYYY-MM-DD format"}), 400

    try:
        unit_price = float(payload.get("unit_price"))
        quantity_tons = float(payload.get("quantity_tons"))
    except (TypeError, ValueError):
        return jsonify({"msg": "unit_price and quantity_tons must be numeric"}), 400

    if unit_price < 0 or quantity_tons < 0:
        return jsonify({"msg": "unit_price and quantity_tons must be non-negative"}), 400

    amount = unit_price * quantity_tons

    if sale_type == "forecast":
        entry = SalesForecastEntry(customer_id=customer.id, date=entry_date, amount=amount)
    else:
        entry = SalesActualEntry(customer_id=customer.id, date=entry_date, amount=amount)

    db.session.add(entry)
    db.session.commit()

    return (
        jsonify(
            {
                "entry": {
                    "id": entry.id,
                    "customer_id": entry.customer_id,
                    "date": entry.date.isoformat(),
                    "amount": amount,
                    "sale_type": sale_type,
                    "unit_price": unit_price,
                    "quantity_tons": quantity_tons,
                }
            }
        ),
        201,
    )
