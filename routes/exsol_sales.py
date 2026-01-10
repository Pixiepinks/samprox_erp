from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any

from flask import Blueprint, current_app, jsonify, request
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
    ExsolSalesInvoiceSerial,
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
items_schema = ExsolInventoryItemSchema(many=True)


def _has_exsol_sales_access() -> bool:
    try:
        claims = get_jwt()
    except Exception:
        return False

    role = normalize_role(claims.get("role"))
    return role in {RoleEnum.sales_manager, RoleEnum.sales_executive}


def _build_error(message: str, status: int = 400, details: Any | None = None):
    payload: dict[str, Any] = {"ok": False, "error": message}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status


def _get_exsol_company_id() -> int | None:
    company = Company.query.filter(Company.name == EXSOL_COMPANY_NAME).one_or_none()
    return company.id if company else None


def _serialize_exsol_customer(customer: NonSamproxCustomer) -> dict[str, Any]:
    sales_rep = customer.managed_by
    sales_rep_name = None
    if sales_rep:
        sales_rep_name = sales_rep.name
    elif customer.managed_by_label:
        sales_rep_name = customer.managed_by_label
    return {
        "id": str(customer.id),
        "name": customer.customer_name,
        "city": customer.city,
        "district": customer.district,
        "province": customer.province,
        "sales_rep_id": str(customer.managed_by_user_id) if customer.managed_by_user_id else None,
        "sales_rep_name": sales_rep_name,
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
    if not item.is_serialized:
        return jsonify({"item_id": item_id, "serials": []})

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
    total_qty = 0
    subtotal = Decimal(0)
    total_discount = Decimal(0)
    grand_total = Decimal(0)

    line_serials: dict[str, list[str]] = {}
    if lines:
        serial_rows = (
            ExsolSalesInvoiceSerial.query.filter(
                ExsolSalesInvoiceSerial.invoice_id == invoice.id
            )
            .order_by(ExsolSalesInvoiceSerial.serial_no.asc())
            .all()
        )
        for serial in serial_rows:
            line_serials.setdefault(str(serial.line_id), []).append(serial.serial_no)

    for line in lines:
        qty = line.qty
        total_qty += qty
        if line.mrp is not None:
            subtotal += Decimal(line.mrp) * qty
        else:
            subtotal += Decimal(line.unit_price) * qty
        if line.discount_value is not None:
            total_discount += Decimal(line.discount_value) * qty
        grand_total += Decimal(line.line_total or 0)

    customer = invoice.customer
    return {
        "id": str(invoice.id),
        "invoice_no": invoice.invoice_no,
        "invoice_date": invoice.invoice_date.isoformat(),
        "customer_id": str(invoice.customer_id),
        "customer_name": customer.customer_name if customer else None,
        "city": customer.city if customer else None,
        "district": customer.district if customer else None,
        "province": customer.province if customer else None,
        "sales_rep_id": invoice.sales_rep_id,
        "lines": [
            {
                "id": str(line.id),
                "item_id": line.item_id,
                "qty": line.qty,
                "mrp": _format_money(Decimal(line.mrp)) if line.mrp is not None else None,
                "discount_rate": str(line.discount_rate) if line.discount_rate is not None else None,
                "discount_value": _format_money(Decimal(line.discount_value))
                if line.discount_value is not None
                else None,
                "unit_price": _format_money(Decimal(line.unit_price)),
                "line_total": _format_money(Decimal(line.line_total or 0)),
                "serial_numbers": line_serials.get(str(line.id), []),
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

    sales_rep_raw = payload.get("sales_rep_id")
    if sales_rep_raw in (None, ""):
        errors["sales_rep_id"] = "Sales representative is required."
        sales_rep_id = None
    else:
        sales_rep_id = _parse_int(sales_rep_raw)
        if sales_rep_id is None:
            errors["sales_rep_id"] = "Sales representative must be a number."

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

        item = items_by_id.get(item_id)
        qty = _parse_int(line.get("qty"))
        if qty is None or qty <= 0:
            line_error["qty"] = "Quantity must be a positive number."

        serials = _normalize_serials(line.get("serial_numbers"))
        requires_serials = bool(item and item.is_serialized)
        if requires_serials:
            if not serials:
                line_error["serial_numbers"] = "Serial numbers are required."
            elif qty is not None and qty != len(serials):
                line_error["serial_numbers"] = "Serial count must match the quantity."
        else:
            serials = []

        if serials and requires_serials:
            all_serials.extend(serials)

        mrp = _parse_decimal(line.get("mrp"))
        discount_rate = _parse_discount_rate(line.get("discount_rate") or line.get("trade_discount_rate"))
        discount_value = _parse_decimal(line.get("discount_value"))

        unit_price = _parse_decimal(line.get("unit_price") or line.get("dealer_price"))
        if unit_price is None or unit_price < 0:
            line_error["unit_price"] = "Unit price is required."

        line_total = _parse_decimal(line.get("line_total"))
        if line_total is None or line_total <= 0:
            line_error["line_total"] = "Line total is required."

        if discount_rate is not None and discount_rate < 0:
            line_error["discount_rate"] = "Discount rate must be positive."
        if discount_value is not None and discount_value < 0:
            line_error["discount_value"] = "Discount value must be positive."

        line_errors.append(line_error)
        prepared_lines.append(
            {
                "item_id": item_id,
                "item": item,
                "qty": qty,
                "serials": serials,
                "mrp": mrp,
                "discount_rate": discount_rate,
                "discount_value": discount_value,
                "unit_price": unit_price,
                "line_total": line_total,
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
        if not item or not item.is_serialized:
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
            "customer_id": customer.id,
            "sales_rep_id": sales_rep_id,
        },
        prepared_lines,
        errors,
        line_errors,
    )


def _create_invoice(payload: dict[str, Any]):
    parsed_header, prepared_lines, errors, line_errors = _validate_invoice_payload(payload)
    if errors or any(line_errors):
        details: list[str] = []
        for field, message in errors.items():
            details.append(f"{field}: {message}")
        for idx, line_error in enumerate(line_errors, start=1):
            for field, message in line_error.items():
                details.append(f"line {idx} {field}: {message}")
        return jsonify({"error": "Validation failed.", "details": details}), 400

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

    subtotal = Decimal(0)
    discount_total = Decimal(0)
    grand_total = Decimal(0)

    for line in prepared_lines:
        qty = Decimal(line["qty"] or 0)
        if line["mrp"] is not None:
            subtotal += line["mrp"] * qty
        else:
            subtotal += line["unit_price"] * qty
        if line["discount_value"] is not None:
            discount_total += line["discount_value"] * qty
        grand_total += line["line_total"]

    try:
        session = db.session
        real_session = session if hasattr(session, "in_transaction") else session()
        transaction_ctx = (
            real_session.begin_nested()
            if real_session.in_transaction()
            else real_session.begin()
        )
        with transaction_ctx:
            invoice = ExsolSalesInvoice(
                company_key=EXSOL_COMPANY_KEY,
                invoice_no=parsed_header["invoice_no"],
                invoice_date=parsed_header["invoice_date"],
                customer_id=parsed_header["customer_id"],
                sales_rep_id=parsed_header["sales_rep_id"],
                subtotal=subtotal,
                discount_total=discount_total,
                grand_total=grand_total,
                created_by_user_id=user.id,
            )
            real_session.add(invoice)

            line_models: list[ExsolSalesInvoiceLine] = []
            for line in prepared_lines:
                line_model = ExsolSalesInvoiceLine(
                    invoice=invoice,
                    company_key=EXSOL_COMPANY_KEY,
                    item_id=line["item_id"],
                    qty=line["qty"],
                    mrp=line["mrp"],
                    discount_rate=line["discount_rate"],
                    discount_value=line["discount_value"],
                    unit_price=line["unit_price"],
                    line_total=line["line_total"],
                )
                real_session.add(line_model)
                line_models.append(line_model)

                for serial in line["serials"]:
                    serial_model = ExsolSalesInvoiceSerial(
                        company_key=EXSOL_COMPANY_KEY,
                        invoice=invoice,
                        line=line_model,
                        item_id=line["item_id"],
                        serial_no=serial,
                    )
                    real_session.add(serial_model)
                    production_serial = serial_map.get(serial)
                    if production_serial:
                        production_serial.is_sold = True
        real_session.commit()

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
        current_app.logger.exception(
            {
                "event": "exsol_invoice_create_failed",
                "company_key": EXSOL_COMPANY_KEY,
                "invoice_no": parsed_header.get("invoice_no") if parsed_header else None,
                "user_id": user.id,
                "payload_summary": {
                    "customer_id": str(parsed_header.get("customer_id")) if parsed_header else None,
                    "line_count": len(prepared_lines or []),
                },
            }
        )
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


@invoices_bp.get("/sales-invoices/<string:invoice_id>")
@jwt_required()
def get_sales_invoice(invoice_id: str):
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
