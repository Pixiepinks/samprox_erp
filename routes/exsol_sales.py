from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import (
    Company,
    ExsolInventoryItem,
    ExsolProductionEntry,
    ExsolProductionSerial,
    ExsolSalesInvoice,
    ExsolSalesInvoiceItem,
    RoleEnum,
    User,
    normalize_role,
)
from schemas import ExsolInventoryItemSchema

bp = Blueprint("exsol_sales", __name__, url_prefix="/api/exsol/sales")

EXSOL_COMPANY_NAME = "Exsol Engineering (Pvt) Ltd"
EXSOL_COMPANY_KEY = "EXSOL"
ALLOWED_DISCOUNT_RATES = {26, 31}

items_schema = ExsolInventoryItemSchema(many=True)


def _has_exsol_sales_access() -> bool:
    try:
        claims = get_jwt()
    except Exception:
        return False

    role = normalize_role(claims.get("role"))
    return role in {RoleEnum.sales_manager, RoleEnum.sales_executive}


def _build_error(message: str, status: int = 400, details: dict[str, Any] | None = None):
    payload: dict[str, Any] = {"ok": False, "error": message}
    if details:
        payload["details"] = details
    return jsonify(payload), status


def _get_exsol_company_id() -> int | None:
    company = Company.query.filter(Company.name == EXSOL_COMPANY_NAME).one_or_none()
    return company.id if company else None


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _normalize_serials(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        serials = [str(entry).strip() for entry in value if str(entry).strip()]
    else:
        serials = [entry.strip() for entry in str(value).split(",") if entry.strip()]
    return serials


@bp.get("/items")
@jwt_required()
def list_exsol_items():
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    company_id = _get_exsol_company_id()
    if not company_id:
        return _build_error("Exsol company not configured.", 500)

    items = (
        ExsolInventoryItem.query.filter(
            ExsolInventoryItem.company_id == company_id,
            ExsolInventoryItem.is_active.is_(True),
        )
        .order_by(ExsolInventoryItem.item_name.asc())
        .all()
    )
    return jsonify(items_schema.dump(items))


@bp.get("/available-serials")
@jwt_required()
def list_available_serials():
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    item_code = (request.args.get("item_code") or "").strip()

    query = (
        db.session.query(ExsolProductionSerial, ExsolProductionEntry)
        .join(ExsolProductionEntry, ExsolProductionSerial.entry_id == ExsolProductionEntry.id)
        .filter(ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolProductionSerial.is_sold.is_(False))
    )
    if item_code:
        query = query.filter(ExsolProductionEntry.item_code == item_code)

    serials = (
        query.order_by(ExsolProductionSerial.serial_no.asc())
        .limit(500)
        .all()
    )
    payload = [
        {
            "serial_no": serial.serial_no,
            "item_code": entry.item_code,
            "item_name": entry.item_name,
        }
        for serial, entry in serials
    ]
    return jsonify(payload)


@bp.post("/invoice")
@jwt_required()
def create_invoice():
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    payload = request.get_json(silent=True) or {}
    errors: dict[str, str] = {}

    invoice_no = (payload.get("invoice_no") or "").strip()
    if not invoice_no:
        errors["invoice_no"] = "Invoice No is required."

    invoice_date = _parse_date(payload.get("invoice_date"))
    if not invoice_date:
        errors["invoice_date"] = "Invoice Date is required."

    customer_name = (payload.get("customer_name") or "").strip()
    if not customer_name:
        errors["customer_name"] = "Customer Name is required."

    item_code = (payload.get("item_code") or "").strip()
    if not item_code:
        errors["item_name"] = "Item Name is required."

    serials = _normalize_serials(payload.get("serial_numbers"))
    if not serials:
        errors["serial_numbers"] = "At least one serial number is required."

    quantity = _parse_int(payload.get("quantity"))
    if quantity is None or quantity <= 0:
        errors["quantity"] = "Quantity must be a positive number."
    elif serials and quantity != len(serials):
        errors["quantity"] = "Quantity must match the number of serials selected."

    mrp = _parse_decimal(payload.get("mrp"))
    if mrp is None or mrp <= 0:
        errors["mrp"] = "MRP is required."

    discount_rate = _parse_int(payload.get("discount_rate"))
    if discount_rate not in ALLOWED_DISCOUNT_RATES:
        errors["discount_rate"] = "Trade discount must be 26% or 31%."

    if errors:
        return jsonify({"errors": errors}), 400

    if len(serials) != len(set(serials)):
        return jsonify({"errors": {"serial_numbers": "Duplicate serials are not allowed."}}), 400

    company_id = _get_exsol_company_id()
    if not company_id:
        return _build_error("Exsol company not configured.", 500)

    item = (
        ExsolInventoryItem.query.filter(
            ExsolInventoryItem.company_id == company_id,
            ExsolInventoryItem.item_code == item_code,
            ExsolInventoryItem.is_active.is_(True),
        )
        .one_or_none()
    )
    if not item:
        return jsonify({"errors": {"item_name": "Selected item is not available."}}), 400

    current_user_id = get_jwt_identity()
    user = User.query.get(int(current_user_id)) if current_user_id else None
    if not user:
        return _build_error("Unable to resolve the current user.", 401)

    serial_rows = (
        db.session.query(ExsolProductionSerial)
        .join(ExsolProductionEntry, ExsolProductionSerial.entry_id == ExsolProductionEntry.id)
        .filter(ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolProductionSerial.serial_no.in_(serials))
        .filter(ExsolProductionSerial.is_sold.is_(False))
        .filter(ExsolProductionEntry.item_code == item_code)
        .all()
    )
    found_serials = {row.serial_no for row in serial_rows}
    missing_serials = sorted(set(serials) - found_serials)
    if missing_serials:
        return jsonify(
            {
                "errors": {
                    "serial_numbers": "Some serial numbers are unavailable or already sold.",
                    "missing": missing_serials,
                }
            }
        ), 400

    discount_value = (mrp * Decimal(discount_rate) / Decimal(100)).quantize(Decimal("0.01"))
    dealer_price = (mrp - discount_value).quantize(Decimal("0.01"))

    try:
        with db.session.begin():
            invoice = ExsolSalesInvoice(
                company_name=EXSOL_COMPANY_NAME,
                invoice_no=invoice_no,
                invoice_date=invoice_date,
                customer_name=customer_name,
                city=(payload.get("city") or "").strip() or None,
                district=(payload.get("district") or "").strip() or None,
                province=(payload.get("province") or "").strip() or None,
                sales_rep_id=user.id,
                sales_rep_name=user.name,
                created_by_user_id=user.id,
            )
            db.session.add(invoice)

            for serial in serial_rows:
                serial.is_sold = True
                item_row = ExsolSalesInvoiceItem(
                    invoice=invoice,
                    item_name=item.item_name,
                    serial_number=serial.serial_no,
                    quantity=1,
                    mrp=mrp,
                    discount_rate=discount_rate,
                    discount_value=discount_value,
                    dealer_price=dealer_price,
                )
                db.session.add(item_row)
    except IntegrityError:
        db.session.rollback()
        return jsonify(
            {
                "errors": {
                    "invoice_no": "Invoice number already exists.",
                    "serial_numbers": "One or more serial numbers are already sold.",
                }
            }
        ), 400
    except SQLAlchemyError:
        db.session.rollback()
        return _build_error("Unable to save the invoice right now.", 500)

    return jsonify({"ok": True, "invoice_no": invoice_no}), 201
