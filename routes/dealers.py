from __future__ import annotations

from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from models import NonSamproxCustomer

bp = Blueprint("dealers", __name__, url_prefix="/api/dealers")


@bp.get("")
@jwt_required()
def list_dealers():
    customers = NonSamproxCustomer.query.order_by(NonSamproxCustomer.customer_code.asc()).all()
    data = [
        {
            "customer_code": customer.customer_code,
            "customer_name": customer.customer_name,
            "area_code": customer.area_code,
            "city": customer.city,
            "district": customer.district,
            "province": customer.province,
            "managed_by": customer.managed_by_label or getattr(customer.managed_by, "name", None),
            "company": customer.company_label or getattr(customer.company, "name", None),
        }
        for customer in customers
    ]
    return jsonify(data)
