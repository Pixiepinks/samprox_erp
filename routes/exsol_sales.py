from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
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
    ExsolSalesInvoiceLine,
    NonSamproxCustomer,
    RoleEnum,
    User,
    normalize_role,
)
from schemas import ExsolInventoryItemSchema

bp = Blueprint("exsol_sales", __name__, url_prefix="/api/exsol/sales")
invoices_bp = Blueprint("exsol_sales_invoices", __name__, url_prefix="/api/exsol")

EXSOL_COMPANY_NAME = "Exsol Engineering (Pvt) Ltd"
EXSOL_COMPANY_KEY = "EXSOL"
ALLOWED_DISCOUNT_RATES = {Decimal("0.26"), Decimal("0.31")}

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


def _serialize_exsol_customer(customer: NonSamproxCustomer) -> dict[str, Any]:
    return {
        "id": str(customer.id),
        "name": customer.customer_name,
        "city": customer.city,
        "district": customer.district,
        "province": customer.province,
    }


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


def _parse_discount_rate(value: Any) -> Decimal | None:
    parsed = _parse_decimal(value)
    if parsed is None:
        return None
    if parsed > 1:
        parsed = (parsed / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return parsed


def _normalize_serials(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        serials = [str(entry).strip() for entry in value if str(entry).strip()]
    else:
        serials = [entry.strip() for entry in re.split(r"[\s,]+", str(value)) if entry.strip()]
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


@invoices_bp.get("/customers")
@jwt_required()
def list_exsol_customers():
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    company_id = _get_exsol_company_id()
    if not company_id:
        return _build_error("Exsol company not configured.", 500)

    customers = (
        NonSamproxCustomer.query.filter(
            NonSamproxCustomer.company_id == company_id,
            NonSamproxCustomer.is_active.is_(True),
        )
        .order_by(NonSamproxCustomer.customer_name.asc())
        .all()
    )
    return jsonify([_serialize_exsol_customer(customer) for customer in customers])


@invoices_bp.get("/serials/available")
@jwt_required()
def list_exsol_available_serials():
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    item_id = (request.args.get("item_id") or "").strip()
    if not item_id:
        return jsonify({"item_id": item_id, "serials": []})

    company_id = _get_exsol_company_id()
    if not company_id:
        return _build_error("Exsol company not configured.", 500)

    item = (
        ExsolInventoryItem.query.filter(
            ExsolInventoryItem.company_id == company_id,
            ExsolInventoryItem.id == item_id,
            ExsolInventoryItem.is_active.is_(True),
        )
        .one_or_none()
    )
    if not item:
        return _build_error("Item not found.", 404)

    serial_rows = (
        db.session.query(ExsolProductionSerial)
        .join(ExsolProductionEntry, ExsolProductionSerial.entry_id == ExsolProductionEntry.id)
        .filter(ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolProductionSerial.is_sold.is_(False))
        .filter(ExsolProductionEntry.item_code == item.item_code)
        .order_by(ExsolProductionSerial.serial_no.asc())
        .limit(500)
        .all()
    )
    return jsonify({"item_id": item_id, "serials": [row.serial_no for row in serial_rows]})


def _format_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"


def _build_invoice_response(invoice: ExsolSalesInvoice, lines: list[ExsolSalesInvoiceLine]):
    total_qty = sum(line.quantity for line in lines)
    subtotal = sum(Decimal(line.mrp) * line.quantity for line in lines)
    total_discount = sum(Decimal(line.discount_value) * line.quantity for line in lines)
    grand_total = sum(Decimal(line.line_total or 0) for line in lines)
    return {
        "id": invoice.id,
        "invoice_no": invoice.invoice_no,
        "invoice_date": invoice.invoice_date.isoformat(),
        "customer_name": invoice.customer_name,
        "city": invoice.city,
        "district": invoice.district,
        "province": invoice.province,
        "sales_representative": invoice.sales_rep_name,
        "lines": [
            {
                "id": line.id,
                "item_id": line.item_id,
                "qty": line.quantity,
                "mrp": _format_money(Decimal(line.mrp)),
                "trade_discount_rate": str(line.trade_discount_rate),
                "discount_value": _format_money(Decimal(line.discount_value)),
                "dealer_price": _format_money(Decimal(line.dealer_price)),
                "line_total": _format_money(Decimal(line.line_total or 0)),
                "serial_numbers": line.serials_json,
            }
            for line in lines
        ],
        "totals": {
            "total_qty": total_qty,
            "subtotal": _format_money(subtotal),
            "total_discount": _format_money(total_discount),
            "grand_total": _format_money(grand_total),
        },
    }


def _validate_invoice_payload(payload: dict[str, Any]):
    errors: dict[str, str] = {}
    line_errors: list[dict[str, str]] = []

    invoice_no = (payload.get("invoice_no") or "").strip()
    if not invoice_no:
        errors["invoice_no"] = "Invoice No is required."

    invoice_date = _parse_date(payload.get("invoice_date"))
    if not invoice_date:
        errors["invoice_date"] = "Invoice Date is required."

    customer_id = (payload.get("customer_id") or "").strip()
    if not customer_id:
        errors["customer_id"] = "Customer selection is required."

    lines_payload = payload.get("lines") or []
    if not isinstance(lines_payload, list) or not lines_payload:
        errors["lines"] = "At least one invoice line is required."
        return None, None, errors, line_errors

    company_id = _get_exsol_company_id()
    if not company_id:
        errors["company"] = "Exsol company not configured."
        return None, None, errors, line_errors

    customer = None
    if customer_id:
        customer = (
            NonSamproxCustomer.query.filter(
                NonSamproxCustomer.company_id == company_id,
                NonSamproxCustomer.id == customer_id,
                NonSamproxCustomer.is_active.is_(True),
            )
            .one_or_none()
        )

    if not customer:
        errors["customer_id"] = "Customer selection is required."
        return None, None, errors, line_errors

    item_ids = {str(line.get("item_id")) for line in lines_payload if line.get("item_id")}
    items = (
        ExsolInventoryItem.query.filter(
            ExsolInventoryItem.company_id == company_id,
            ExsolInventoryItem.id.in_(item_ids),
            ExsolInventoryItem.is_active.is_(True),
        )
        .all()
    )
    items_by_id = {str(item.id): item for item in items}

    prepared_lines = []
    all_serials: list[str] = []
    for idx, line in enumerate(lines_payload):
        line_error: dict[str, str] = {}
        item_id = (line.get("item_id") or "").strip()
        if not item_id or item_id not in items_by_id:
            line_error["item_id"] = "Item selection is required."

        qty = _parse_int(line.get("qty"))
        if qty is None or qty <= 0:
            line_error["qty"] = "Quantity must be a positive number."

        serials = _normalize_serials(line.get("serial_numbers"))
        if not serials:
            line_error["serial_numbers"] = "Serial numbers are required."
        elif qty is not None and qty != len(serials):
            line_error["serial_numbers"] = "Serial count must match the quantity."

        if serials:
            all_serials.extend(serials)

        mrp = _parse_decimal(line.get("mrp"))
        if mrp is None or mrp <= 0:
            line_error["mrp"] = "MRP is required."

        discount_rate = _parse_discount_rate(line.get("trade_discount_rate"))
        if discount_rate not in ALLOWED_DISCOUNT_RATES:
            line_error["trade_discount_rate"] = "Trade discount must be 26% or 31%."

        discount_value = _parse_decimal(line.get("discount_value"))
        dealer_price = _parse_decimal(line.get("dealer_price"))

        if not line_error and mrp and discount_rate:
            if discount_value is None:
                discount_value = (mrp * discount_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if dealer_price is None:
                dealer_price = (mrp - discount_value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if discount_value is None or discount_value < 0:
            line_error["discount_value"] = "Discount value is required."
        if dealer_price is None or dealer_price < 0:
            line_error["dealer_price"] = "Dealer price is required."

        line_errors.append(line_error)
        prepared_lines.append(
            {
                "item_id": item_id,
                "item": items_by_id.get(item_id),
                "qty": qty,
                "serials": serials,
                "mrp": mrp,
                "trade_discount_rate": discount_rate,
                "discount_value": discount_value,
                "dealer_price": dealer_price,
            }
        )

    if errors:
        return None, None, errors, line_errors

    if any(line_errors):
        return None, None, errors, line_errors

    if len(all_serials) != len(set(all_serials)):
        errors["serial_numbers"] = "Duplicate serial numbers are not allowed across invoice lines."
        return None, None, errors, line_errors

    serial_rows = (
        db.session.query(ExsolProductionSerial, ExsolProductionEntry)
        .join(ExsolProductionEntry, ExsolProductionSerial.entry_id == ExsolProductionEntry.id)
        .filter(ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolProductionSerial.serial_no.in_(all_serials))
        .filter(ExsolProductionSerial.is_sold.is_(False))
        .all()
    )
    serial_map = {serial.serial_no: (serial, entry.item_code) for serial, entry in serial_rows}

    for idx, prepared in enumerate(prepared_lines):
        item = prepared["item"]
        if not item:
            continue
        missing = [serial for serial in prepared["serials"] if serial not in serial_map]
        mismatched = [
            serial
            for serial in prepared["serials"]
            if serial in serial_map and serial_map[serial][1] != item.item_code
        ]
        if missing:
            line_errors[idx]["serial_numbers"] = "Some serial numbers are unavailable or already sold."
        if mismatched:
            line_errors[idx]["serial_numbers"] = "Serial numbers do not match the selected item."

    if any(line_errors):
        return None, None, errors, line_errors

    return (
        {
            "invoice_no": invoice_no,
            "invoice_date": invoice_date,
            "customer_name": customer.customer_name,
            "city": (customer.city or "").strip() or None,
            "district": (customer.district or "").strip() or None,
            "province": (customer.province or "").strip() or None,
        },
        prepared_lines,
        errors,
        line_errors,
    )


def _create_invoice(payload: dict[str, Any]):
    parsed_header, prepared_lines, errors, line_errors = _validate_invoice_payload(payload)
    if errors or any(line_errors):
        return jsonify({"errors": errors, "line_errors": line_errors}), 400

    current_user_id = get_jwt_identity()
    user = User.query.get(int(current_user_id)) if current_user_id else None
    if not user:
        return _build_error("Unable to resolve the current user.", 401)

    serial_map = {}
    if prepared_lines:
        serials = [serial for line in prepared_lines for serial in line["serials"]]
        serial_rows = (
            db.session.query(ExsolProductionSerial)
            .filter(ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY)
            .filter(ExsolProductionSerial.serial_no.in_(serials))
            .filter(ExsolProductionSerial.is_sold.is_(False))
            .all()
        )
        serial_map = {row.serial_no: row for row in serial_rows}

    try:
        with db.session.begin():
            invoice = ExsolSalesInvoice(
                company_key=EXSOL_COMPANY_KEY,
                company_name=EXSOL_COMPANY_NAME,
                invoice_no=parsed_header["invoice_no"],
                invoice_date=parsed_header["invoice_date"],
                customer_name=parsed_header["customer_name"],
                city=parsed_header["city"],
                district=parsed_header["district"],
                province=parsed_header["province"],
                sales_rep_id=user.id,
                sales_rep_name=user.name,
                created_by_user_id=user.id,
            )
            db.session.add(invoice)

            line_models: list[ExsolSalesInvoiceLine] = []
            for line in prepared_lines:
                line_total = (line["dealer_price"] * Decimal(line["qty"])).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
                line_model = ExsolSalesInvoiceLine(
                    invoice=invoice,
                    item_id=line["item_id"],
                    quantity=line["qty"],
                    mrp=line["mrp"],
                    trade_discount_rate=line["trade_discount_rate"],
                    discount_value=line["discount_value"],
                    dealer_price=line["dealer_price"],
                    line_total=line_total,
                    serials_json=line["serials"],
                )
                db.session.add(line_model)
                line_models.append(line_model)

                for serial in line["serials"]:
                    serial_model = serial_map.get(serial)
                    if serial_model:
                        serial_model.is_sold = True

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

    return jsonify({"ok": True, "invoice": _build_invoice_response(invoice, line_models)}), 201


@bp.post("/invoice")
@jwt_required()
def create_invoice():
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    payload = request.get_json(silent=True) or {}
    return _create_invoice(payload)


@invoices_bp.post("/sales-invoices")
@jwt_required()
def create_sales_invoice():
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    payload = request.get_json(silent=True) or {}
    return _create_invoice(payload)


@invoices_bp.get("/sales-invoices/<int:invoice_id>")
@jwt_required()
def get_sales_invoice(invoice_id: int):
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    invoice = ExsolSalesInvoice.query.get(invoice_id)
    if not invoice:
        return _build_error("Invoice not found.", 404)

    lines = (
        ExsolSalesInvoiceLine.query.filter(ExsolSalesInvoiceLine.invoice_id == invoice.id)
        .order_by(ExsolSalesInvoiceLine.id.asc())
        .all()
    )
    return jsonify({"ok": True, "invoice": _build_invoice_response(invoice, lines)})
