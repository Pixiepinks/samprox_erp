from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import func

from extensions import db
from models import (
    Customer,
    CustomerCategory,
    CustomerCreditTerm,
    CustomerTransportMode,
    CustomerType,
    SalesActualEntry,
    SalesForecastEntry,
    TeamMember,
)

bp = Blueprint("market", __name__, url_prefix="/api/market")


def _serialize_sale_entry(entry, sale_type: str):
    payload = {
        "id": entry.id,
        "customer_id": entry.customer_id,
        "date": entry.date.isoformat() if entry.date else None,
        "amount": float(entry.amount or 0.0),
        "sale_type": sale_type,
        "unit_price": float(entry.unit_price or 0.0),
        "quantity_tons": float(entry.quantity_tons or 0.0),
    }

    if sale_type == "actual":
        payload.update(
            {
                "delivery_note_number": getattr(entry, "delivery_note_number", None),
                "weigh_slip_number": getattr(entry, "weigh_slip_number", None),
                "loader1_id": getattr(entry, "loader1_id", None),
                "loader2_id": getattr(entry, "loader2_id", None),
                "loader3_id": getattr(entry, "loader3_id", None),
            }
        )

    return payload


def _parse_sale_payload(payload):
    try:
        customer_id = int(payload.get("customer_id"))
    except (TypeError, ValueError):
        return None, None, None, (jsonify({"msg": "A valid customer_id is required"}), 400)

    customer = Customer.query.get(customer_id)
    if not customer:
        return None, None, None, (jsonify({"msg": "Customer not found"}), 404)

    sale_type = (payload.get("sale_type") or "").strip().lower()
    if sale_type not in {"actual", "forecast"}:
        return None, None, None, (
            jsonify({"msg": "sale_type must be either 'actual' or 'forecast'"}),
            400,
        )

    date_str = payload.get("date")
    try:
        entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None, None, None, (
            jsonify({"msg": "date must be provided in YYYY-MM-DD format"}),
            400,
        )

    try:
        unit_price = float(payload.get("unit_price"))
        quantity_tons = float(payload.get("quantity_tons"))
    except (TypeError, ValueError):
        return None, None, None, (
            jsonify({"msg": "unit_price and quantity_tons must be numeric"}),
            400,
        )

    if unit_price < 0 or quantity_tons < 0:
        return None, None, None, (
            jsonify({"msg": "unit_price and quantity_tons must be non-negative"}),
            400,
        )

    amount = unit_price * quantity_tons

    entry_kwargs = dict(
        customer_id=customer.id,
        date=entry_date,
        amount=amount,
        unit_price=unit_price,
        quantity_tons=quantity_tons,
    )

    if sale_type == "actual":

        def _extract_text(field_name: str, label: str, required: bool = False) -> str | None:
            raw_value = payload.get(field_name)
            value = (raw_value or "").strip()
            if required and not value:
                raise ValueError(f"{label} is required for actual sale entries.")
            return value or None

        def _parse_loader(field_name: str, label: str, required: bool = False) -> int | None:
            raw_value = payload.get(field_name)
            if raw_value in (None, ""):
                if required:
                    raise ValueError(f"{label} is required for actual sale entries.")
                return None

            try:
                loader_id = int(raw_value)
            except (TypeError, ValueError):
                raise ValueError(f"{label} must reference a valid team member.") from None

            loader = TeamMember.query.get(loader_id)
            if not loader:
                raise ValueError(f"{label} must reference an existing team member.")
            return loader.id

        try:
            delivery_note_number = _extract_text("delivery_note_number", "Delivery Note No", required=True)
            weigh_slip_number = _extract_text("weigh_slip_number", "Weigh Slip No")
            loader1_id = _parse_loader("loader1_id", "Loader 1 Name", required=True)
            loader2_id = _parse_loader("loader2_id", "Loader 2 Name")
            loader3_id = _parse_loader("loader3_id", "Loader 3 Name")
        except ValueError as error:
            return None, None, None, (jsonify({"msg": str(error)}), 400)

        entry_kwargs.update(
            delivery_note_number=delivery_note_number,
            weigh_slip_number=weigh_slip_number,
            loader1_id=loader1_id,
            loader2_id=loader2_id,
            loader3_id=loader3_id,
        )

    return sale_type, amount, entry_kwargs, None


def _serialize_customer(customer: Customer):
    return {
        "id": customer.id,
        "code": customer.code,
        "name": customer.name,
        "category": customer.category.value,
        "credit_term": customer.credit_term.value,
        "transport_mode": customer.transport_mode.value,
        "customer_type": customer.customer_type.value,
        "sales_coordinator_name": customer.sales_coordinator_name,
        "sales_coordinator_phone": customer.sales_coordinator_phone,
        "store_keeper_name": customer.store_keeper_name,
        "store_keeper_phone": customer.store_keeper_phone,
        "payment_coordinator_name": customer.payment_coordinator_name,
        "payment_coordinator_phone": customer.payment_coordinator_phone,
        "special_note": customer.special_note,
    }


@bp.get("/customers")
@jwt_required()
def list_customers():
    customers = Customer.query.order_by(Customer.name.asc()).all()
    return jsonify({"customers": [_serialize_customer(customer) for customer in customers]})


@bp.post("/customers")
@jwt_required()
def create_customer():
    payload = request.get_json(silent=True) or {}

    def _required_text(field_name, label):
        value = (payload.get(field_name) or "").strip()
        if not value:
            raise ValueError(f"{label} is required")
        return value

    def _parse_enum(field_name, enum_cls, label):
        raw_value = (payload.get(field_name) or "").strip()
        if not raw_value:
            raise ValueError(f"{label} is required")

        try:
            return enum_cls(raw_value)
        except ValueError:
            pass

        normalized_value = "".join(ch for ch in raw_value.lower() if ch.isalnum())
        for member in enum_cls:
            if normalized_value in {
                "".join(ch for ch in member.value.lower() if ch.isalnum()),
                "".join(ch for ch in member.name.lower() if ch.isalnum()),
            }:
                return member

        valid_values = ", ".join(sorted(member.value for member in enum_cls))
        raise ValueError(f"{label} must be one of: {valid_values}")

    try:
        name = _required_text("name", "Customer name")
        category = _parse_enum("category", CustomerCategory, "Category")
        credit_term = _parse_enum("credit_term", CustomerCreditTerm, "Credit term")
        transport_mode = _parse_enum("transport_mode", CustomerTransportMode, "Transport mode")
        customer_type = _parse_enum("customer_type", CustomerType, "Customer type")
        sales_coordinator_name = _required_text(
            "sales_coordinator_name", "Sales coordinator name"
        )
        sales_coordinator_phone = _required_text(
            "sales_coordinator_phone", "Sales coordinator telephone"
        )
        store_keeper_name = _required_text("store_keeper_name", "Store keeper name")
        store_keeper_phone = _required_text(
            "store_keeper_phone", "Store keeper telephone"
        )
        payment_coordinator_name = _required_text(
            "payment_coordinator_name", "Payment coordinator name"
        )
        payment_coordinator_phone = _required_text(
            "payment_coordinator_phone", "Payment coordinator telephone"
        )
        special_note = (payload.get("special_note") or "").strip()
    except ValueError as error:
        return jsonify({"msg": str(error)}), 400

    existing = Customer.query.filter(func.lower(Customer.name) == name.lower()).first()
    if existing:
        return (
            jsonify(
                {
                    "msg": "A customer with this name already exists.",
                    "customer": _serialize_customer(existing),
                }
            ),
            409,
        )

    customer = Customer(
        name=name,
        category=category,
        credit_term=credit_term,
        transport_mode=transport_mode,
        customer_type=customer_type,
        sales_coordinator_name=sales_coordinator_name,
        sales_coordinator_phone=sales_coordinator_phone,
        store_keeper_name=store_keeper_name,
        store_keeper_phone=store_keeper_phone,
        payment_coordinator_name=payment_coordinator_name,
        payment_coordinator_phone=payment_coordinator_phone,
        special_note=special_note,
    )
    db.session.add(customer)
    db.session.commit()

    return jsonify({"customer": _serialize_customer(customer)}), 201


@bp.get("/sales")
@jwt_required()
def fetch_sales_entry():
    entry_id_param = request.args.get("entry_id")
    sale_type_param = (request.args.get("sale_type") or "").strip().lower()

    def _not_found():
        return jsonify({"msg": "Sales entry not found"}), 404

    if entry_id_param is not None:
        try:
            entry_id = int(entry_id_param)
        except (TypeError, ValueError):
            return jsonify({"msg": "entry_id must be an integer"}), 400

        resolved_type = sale_type_param if sale_type_param in {"actual", "forecast"} else None
        entry = None

        if resolved_type == "forecast":
            entry = SalesForecastEntry.query.get(entry_id)
        elif resolved_type == "actual":
            entry = SalesActualEntry.query.get(entry_id)
        else:
            entry = SalesActualEntry.query.get(entry_id)
            if entry:
                resolved_type = "actual"
            else:
                entry = SalesForecastEntry.query.get(entry_id)
                if entry:
                    resolved_type = "forecast"

        if not entry or not resolved_type:
            return _not_found()

        return jsonify({"entry": _serialize_sale_entry(entry, resolved_type)})

    try:
        customer_id = int(request.args.get("customer_id"))
    except (TypeError, ValueError):
        return jsonify({"msg": "A valid customer_id is required"}), 400

    sale_type = sale_type_param
    if sale_type not in {"actual", "forecast"}:
        return (
            jsonify({"msg": "sale_type must be either 'actual' or 'forecast'"}),
            400,
        )

    date_str = request.args.get("date")
    if not date_str:
        return jsonify({"msg": "date parameter is required"}), 400

    try:
        entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return (
            jsonify({"msg": "date must be provided in YYYY-MM-DD format"}),
            400,
        )

    if sale_type == "forecast":
        entry = (
            SalesForecastEntry.query.filter_by(customer_id=customer_id)
            .filter(SalesForecastEntry.date == entry_date)
            .order_by(SalesForecastEntry.id.desc())
            .first()
        )
    else:
        entry = (
            SalesActualEntry.query.filter_by(customer_id=customer_id)
            .filter(SalesActualEntry.date == entry_date)
            .order_by(SalesActualEntry.id.desc())
            .first()
        )

    if not entry:
        return _not_found()

    return jsonify({"entry": _serialize_sale_entry(entry, sale_type)})


@bp.post("/sales")
@jwt_required()
def record_sale_entry():
    payload = request.get_json(silent=True) or {}

    sale_type, _, entry_kwargs, error = _parse_sale_payload(payload)
    if error:
        return error

    if sale_type == "forecast":
        entry = SalesForecastEntry(**entry_kwargs)
    else:
        entry = SalesActualEntry(**entry_kwargs)

    db.session.add(entry)
    db.session.commit()

    return jsonify({"entry": _serialize_sale_entry(entry, sale_type)}), 201


@bp.put("/sales/<int:entry_id>")
@jwt_required()
def update_sale_entry(entry_id: int):
    payload = request.get_json(silent=True) or {}

    sale_type, _, entry_kwargs, error = _parse_sale_payload(payload)
    if error:
        return error

    if sale_type == "forecast":
        entry = SalesForecastEntry.query.get(entry_id)
    else:
        entry = SalesActualEntry.query.get(entry_id)

    if not entry:
        return jsonify({"msg": "Sales entry not found"}), 404

    for field, value in entry_kwargs.items():
        setattr(entry, field, value)

    db.session.commit()

    return jsonify({"entry": _serialize_sale_entry(entry, sale_type)})
