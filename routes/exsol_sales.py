from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required, verify_jwt_in_request
from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models import (
    Company,
    ExsolInventoryItem,
    ExsolSerialEvent,
    ExsolProductionEntry,
    ExsolProductionSerial,
    ExsolSalesInvoice,
    ExsolSalesInvoiceLine,
    ExsolSalesInvoiceSerial,
    ExsolSalesReturn,
    ExsolSalesReturnLine,
    ExsolSalesReturnSerial,
    NonSamproxCustomer,
    RoleEnum,
    SALES_MANAGER_ROLES,
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
    """
    API access control for Exsol sales endpoints.
    Allow: admin, sales_manager, sales_executive
    For non-admin, require company_key == 'exsol-engineering'.
    """
    try:
        # Optional so endpoints can still return False cleanly if missing
        verify_jwt_in_request(optional=True)
        claims = get_jwt() or {}
    except Exception:
        claims = {}

    role = normalize_role(claims.get("role"))
    company_key = (claims.get("company_key") or claims.get("company") or "").strip().lower()

    # Fallback to DB user if JWT claims are incomplete
    if role is None or not company_key:
        identity = get_jwt_identity()
        try:
            user_id = int(identity) if identity is not None else None
        except (TypeError, ValueError):
            user_id = None

        if user_id:
            user = User.query.get(user_id)
            if user:
                if role is None:
                    role = user.role
                if not company_key:
                    company_key = (user.company_key or "").strip().lower()

    allowed_roles = {RoleEnum.admin, RoleEnum.sales_manager, RoleEnum.sales_executive}
    if role not in allowed_roles:
        return False

    if role == RoleEnum.admin:
        return True

    return company_key == "exsol-engineering"


def _build_error(message: str, status: int = 400, details: Any | None = None):
    payload: dict[str, Any] = {"ok": False, "error": message}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status


def _get_exsol_company_id() -> int | None:
    company = Company.query.filter(Company.name == EXSOL_COMPANY_NAME).one_or_none()
    return company.id if company else None


def _add_months(source: date, months: int) -> date:
    month_index = source.month - 1 + months
    year = source.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(source.day, last_day))


def _has_exsol_sales_manager_access() -> bool:
    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt() or {}
    except Exception:
        claims = {}

    role = normalize_role(claims.get("role"))
    company_key = (claims.get("company_key") or claims.get("company") or "").strip().lower()

    if role is None or not company_key:
        identity = get_jwt_identity()
        try:
            user_id = int(identity) if identity is not None else None
        except (TypeError, ValueError):
            user_id = None

        if user_id:
            user = User.query.get(user_id)
            if user:
                role = role or user.role
                company_key = company_key or (user.company_key or "").strip().lower()

    if role == RoleEnum.admin:
        return True

    if role not in SALES_MANAGER_ROLES or role == RoleEnum.sales_executive:
        return False

    return company_key == "exsol-engineering"


def _coerce_metric_value(value: Any, metric: str) -> float | int:
    if value is None:
        return 0
    if isinstance(value, Decimal):
        numeric = float(value)
    else:
        numeric = float(value)
    if metric == "qty":
        return int(round(numeric))
    return float(numeric)


def _resolve_salesperson_label(sales_rep_id: int | None, sales_rep_name: str | None) -> str:
    if sales_rep_name:
        return sales_rep_name
    if sales_rep_id is not None:
        return f"Sales Rep {sales_rep_id}"
    return "Unknown"


def _exsol_sales_status_filter():
    return or_(
        ExsolSalesInvoice.status.is_(None),
        ~func.lower(ExsolSalesInvoice.status).in_(["cancelled", "canceled", "voided", "void"]),
    )


def _exsol_sales_line_query(*, start_date: date, end_date: date, company_id: int):
    return (
        db.session.query(ExsolSalesInvoiceLine)
        .join(ExsolSalesInvoice, ExsolSalesInvoiceLine.invoice_id == ExsolSalesInvoice.id)
        .join(ExsolInventoryItem, ExsolInventoryItem.id == ExsolSalesInvoiceLine.item_id)
        .filter(ExsolSalesInvoice.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolSalesInvoiceLine.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolSalesInvoice.invoice_date >= start_date)
        .filter(ExsolSalesInvoice.invoice_date <= end_date)
        .filter(_exsol_sales_status_filter())
        .filter(ExsolInventoryItem.company_id == company_id)
    )


def _exsol_amount_expression():
    return case(
        (ExsolSalesInvoiceLine.line_total.is_(None), ExsolSalesInvoiceLine.qty * ExsolSalesInvoiceLine.unit_price),
        else_=ExsolSalesInvoiceLine.line_total,
    )


def _fetch_stacked_sales_data(
    *,
    start_date: date,
    end_date: date,
    item_codes: list[str],
    metric: str,
    company_id: int,
) -> list[tuple[int | None, str | None, str, Decimal]]:
    amount_expr = _exsol_amount_expression()
    metric_expr = func.sum(amount_expr if metric == "amount" else ExsolSalesInvoiceLine.qty)
    status_filter = _exsol_sales_status_filter()

    query = (
        db.session.query(
            ExsolSalesInvoice.sales_rep_id,
            User.name.label("sales_rep_name"),
            ExsolInventoryItem.item_code,
            metric_expr.label("value"),
        )
        .join(ExsolSalesInvoiceLine, ExsolSalesInvoiceLine.invoice_id == ExsolSalesInvoice.id)
        .join(ExsolInventoryItem, ExsolInventoryItem.id == ExsolSalesInvoiceLine.item_id)
        .outerjoin(User, User.id == ExsolSalesInvoice.sales_rep_id)
        .filter(ExsolSalesInvoice.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolSalesInvoiceLine.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolSalesInvoice.invoice_date >= start_date)
        .filter(ExsolSalesInvoice.invoice_date <= end_date)
        .filter(status_filter)
        .filter(ExsolInventoryItem.company_id == company_id)
    )

    if item_codes:
        query = query.filter(ExsolInventoryItem.item_code.in_(item_codes))

    query = query.group_by(
        ExsolSalesInvoice.sales_rep_id,
        User.name,
        ExsolInventoryItem.item_code,
    )

    return list(query.all())


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


def _parse_item_codes(*keys: str) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for key in keys:
        for raw_value in request.args.getlist(key):
            for entry in (raw_value or "").split(","):
                code = entry.strip()
                if code and code not in seen:
                    codes.append(code)
                    seen.add(code)
    return codes


def _build_water_pump_filter(item_codes: list[str]):
    if item_codes:
        return ExsolInventoryItem.item_code.in_(item_codes)
    return ExsolInventoryItem.item_code.ilike("EXS%")


def _sum_exsol_sales_amount(
    *,
    start_date: date,
    end_date: date,
    company_id: int,
    item_codes: list[str],
) -> float:
    query = _exsol_sales_line_query(start_date=start_date, end_date=end_date, company_id=company_id)
    if item_codes:
        query = query.filter(ExsolInventoryItem.item_code.in_(item_codes))
    amount_expr = _exsol_amount_expression()
    total = query.with_entities(func.coalesce(func.sum(amount_expr), 0)).scalar()
    return float(total or 0)


def _sum_exsol_water_pump_qty(
    *,
    start_date: date,
    end_date: date,
    company_id: int,
    item_codes: list[str],
) -> int:
    query = _exsol_sales_line_query(start_date=start_date, end_date=end_date, company_id=company_id)
    query = query.filter(_build_water_pump_filter(item_codes))
    total = query.with_entities(func.coalesce(func.sum(ExsolSalesInvoiceLine.qty), 0)).scalar()
    return _coerce_metric_value(total, "qty")


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


@bp.get("/dashboard/stacked-sales")
@jwt_required()
def exsol_stacked_sales_dashboard():
    if not _has_exsol_sales_manager_access():
        return _build_error("Access denied", 403)

    start_date = _parse_date(request.args.get("start"))
    end_date = _parse_date(request.args.get("end"))
    if not start_date or not end_date:
        return _build_error("Start and end dates are required.", 400)

    compare = (request.args.get("compare") or "0").strip().lower() in {"1", "true", "yes"}
    metric = (request.args.get("metric") or "amount").strip().lower()
    if metric not in {"amount", "qty"}:
        return _build_error("Invalid metric selection.", 400)

    items_raw = (request.args.get("items") or "").strip()
    item_codes = [code.strip() for code in items_raw.split(",") if code.strip()] if items_raw else []

    company_id = _get_exsol_company_id()
    if not company_id:
        return _build_error("Exsol company not configured.", 500)

    current_rows = _fetch_stacked_sales_data(
        start_date=start_date,
        end_date=end_date,
        item_codes=item_codes,
        metric=metric,
        company_id=company_id,
    )

    previous_rows: list[tuple[int | None, str | None, str, Decimal]] = []
    prev_start = prev_end = None
    if compare:
        prev_start = _add_months(start_date, -1)
        prev_end = _add_months(end_date, -1)
        previous_rows = _fetch_stacked_sales_data(
            start_date=prev_start,
            end_date=prev_end,
            item_codes=item_codes,
            metric=metric,
            company_id=company_id,
        )

    series_current: dict[str, dict[str, float | int]] = {}
    series_previous: dict[str, dict[str, float | int]] = {}
    item_code_set: set[str] = set()
    label_set: set[str] = set()

    for sales_rep_id, sales_rep_name, item_code, value in current_rows:
        label = _resolve_salesperson_label(sales_rep_id, sales_rep_name)
        label_set.add(label)
        item_code_set.add(item_code)
        series_current.setdefault(label, {})[item_code] = _coerce_metric_value(value, metric)

    for sales_rep_id, sales_rep_name, item_code, value in previous_rows:
        label = _resolve_salesperson_label(sales_rep_id, sales_rep_name)
        label_set.add(label)
        item_code_set.add(item_code)
        series_previous.setdefault(label, {})[item_code] = _coerce_metric_value(value, metric)

    labels = list(label_set)
    item_codes_out = sorted(item_code_set)

    totals_current = {label: sum(series_current.get(label, {}).values()) for label in labels}
    if compare and all(total == 0 for total in totals_current.values()):
        totals = {label: sum(series_previous.get(label, {}).values()) for label in labels}
    else:
        totals = totals_current

    labels_sorted = sorted(labels, key=lambda label: totals.get(label, 0), reverse=True)

    payload = {
        "range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "compare": compare,
        "metric": metric,
        "unit": "LKR" if metric == "amount" else "UNITS",
        "labels": labels_sorted,
        "item_codes": item_codes_out,
        "series": {
            "current": series_current,
            "previous": series_previous if compare else {},
        },
    }

    if compare and prev_start and prev_end:
        payload["previous_range"] = {"start": prev_start.isoformat(), "end": prev_end.isoformat()}

    return jsonify(payload)


@bp.get("/mtd-summary")
@jwt_required()
def exsol_mtd_summary():
    if not _has_exsol_sales_manager_access():
        return _build_error("Access denied", 403)

    start_date = _parse_date(request.args.get("start_date") or request.args.get("start"))
    end_date = _parse_date(request.args.get("end_date") or request.args.get("end"))
    if not start_date or not end_date:
        return _build_error("Start and end dates are required.", 400)

    item_codes = _parse_item_codes("item_codes", "items")

    company_id = _get_exsol_company_id()
    if not company_id:
        return _build_error("Exsol company not configured.", 500)

    sales_amount = _sum_exsol_sales_amount(
        start_date=start_date,
        end_date=end_date,
        company_id=company_id,
        item_codes=item_codes,
    )
    water_pump_qty = _sum_exsol_water_pump_qty(
        start_date=start_date,
        end_date=end_date,
        company_id=company_id,
        item_codes=item_codes,
    )

    return jsonify(
        {
            "sales_amount_lkr": sales_amount,
            "water_pump_qty": water_pump_qty,
        }
    )


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


def _generate_sales_return_no(*, company_id: int, session) -> str:
    prefix = "SR-"
    last_return = (
        session.query(ExsolSalesReturn.return_no)
        .filter(ExsolSalesReturn.company_id == company_id)
        .filter(ExsolSalesReturn.return_no.ilike(f"{prefix}%"))
        .order_by(ExsolSalesReturn.return_no.desc())
        .limit(1)
        .scalar()
    )
    next_number = 1
    if last_return:
        match = re.search(r"SR-(\d+)", last_return)
        if match:
            next_number = int(match.group(1)) + 1
    return f"{prefix}{next_number:06d}"


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

    customer = NonSamproxCustomer.query.get(parsed_header["customer_id"]) if parsed_header else None

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
            event_rows: list[ExsolSerialEvent] = []
            event_date = datetime.combine(invoice.invoice_date, datetime.min.time())
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
                        status="SOLD",
                    )
                    real_session.add(serial_model)
                    production_serial = serial_map.get(serial)
                    if production_serial:
                        production_serial.is_sold = True
                    if line.get("item"):
                        event_rows.append(
                            ExsolSerialEvent(
                                company_key=EXSOL_COMPANY_KEY,
                                item_code=line["item"].item_code,
                                serial_number=serial,
                                event_type="INVOICED",
                                event_date=event_date,
                                ref_type="SALES_INVOICE",
                                ref_id=str(invoice.id),
                                ref_no=invoice.invoice_no,
                                customer_id=invoice.customer_id,
                                customer_name=customer.customer_name if customer else None,
                            )
                        )
            if event_rows:
                real_session.add_all(event_rows)
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


@invoices_bp.get("/serials/<string:serial_number>/timeline")
@jwt_required(optional=True)
def get_serial_timeline(serial_number: str):
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    serial_number = (serial_number or "").strip()
    if not serial_number:
        return _build_error("Serial number is required.", 400)

    item_code = (request.args.get("item_code") or "").strip()
    if not item_code:
        return _build_error("Item code is required.", 400)

    events = (
        ExsolSerialEvent.query.filter(
            ExsolSerialEvent.company_key == EXSOL_COMPANY_KEY,
            ExsolSerialEvent.item_code == item_code,
            ExsolSerialEvent.serial_number == serial_number,
        )
        .order_by(ExsolSerialEvent.event_date.asc(), ExsolSerialEvent.created_at.asc())
        .all()
    )

    def _serialize_event(event: ExsolSerialEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "event_type": event.event_type,
            "event_date": event.event_date.isoformat() if event.event_date else None,
            "ref_type": event.ref_type,
            "ref_id": event.ref_id,
            "ref_no": event.ref_no,
            "customer_id": str(event.customer_id) if event.customer_id else None,
            "customer_name": event.customer_name,
            "notes": event.notes,
            "meta_json": event.meta_json,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }

    timeline_events = [_serialize_event(event) for event in events]

    if not timeline_events:
        synthesized: list[dict[str, Any]] = []

        production_row = (
            db.session.query(ExsolProductionSerial, ExsolProductionEntry)
            .join(ExsolProductionEntry, ExsolProductionSerial.entry_id == ExsolProductionEntry.id)
            .filter(ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY)
            .filter(ExsolProductionSerial.serial_no == serial_number)
            .filter(ExsolProductionEntry.item_code == item_code)
            .first()
        )
        if production_row:
            serial, entry = production_row
            synthesized.append(
                {
                    "id": None,
                    "event_type": "PRODUCED",
                    "event_date": entry.created_at.isoformat() if entry.created_at else None,
                    "ref_type": "PRODUCTION_ENTRY",
                    "ref_id": str(entry.id),
                    "ref_no": None,
                    "customer_id": None,
                    "customer_name": None,
                    "notes": None,
                    "meta_json": None,
                    "created_at": serial.created_at.isoformat() if serial.created_at else None,
                }
            )

        invoice_rows = (
            db.session.query(ExsolSalesInvoiceSerial, ExsolSalesInvoice, NonSamproxCustomer)
            .join(ExsolSalesInvoice, ExsolSalesInvoiceSerial.invoice_id == ExsolSalesInvoice.id)
            .join(ExsolInventoryItem, ExsolSalesInvoiceSerial.item_id == ExsolInventoryItem.id)
            .join(NonSamproxCustomer, NonSamproxCustomer.id == ExsolSalesInvoice.customer_id)
            .filter(ExsolSalesInvoiceSerial.company_key == EXSOL_COMPANY_KEY)
            .filter(ExsolSalesInvoiceSerial.serial_no == serial_number)
            .filter(ExsolInventoryItem.item_code == item_code)
            .all()
        )
        for serial, invoice, customer in invoice_rows:
            synthesized.append(
                {
                    "id": None,
                    "event_type": "INVOICED",
                    "event_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
                    "ref_type": "SALES_INVOICE",
                    "ref_id": str(invoice.id),
                    "ref_no": invoice.invoice_no,
                    "customer_id": str(invoice.customer_id),
                    "customer_name": customer.customer_name,
                    "notes": None,
                    "meta_json": None,
                    "created_at": serial.created_at.isoformat() if serial.created_at else None,
                }
            )

        return_rows = (
            db.session.query(ExsolSalesReturnSerial, ExsolSalesReturnLine, ExsolSalesReturn, NonSamproxCustomer)
            .join(ExsolSalesReturnLine, ExsolSalesReturnSerial.return_line_id == ExsolSalesReturnLine.id)
            .join(ExsolSalesReturn, ExsolSalesReturnLine.return_id == ExsolSalesReturn.id)
            .join(NonSamproxCustomer, NonSamproxCustomer.id == ExsolSalesReturn.customer_id)
            .filter(ExsolSalesReturnSerial.serial_number == serial_number)
            .filter(ExsolSalesReturnLine.item_code == item_code)
            .filter(ExsolSalesReturn.company_key == EXSOL_COMPANY_KEY)
            .all()
        )
        for serial, line, return_header, customer in return_rows:
            synthesized.append(
                {
                    "id": None,
                    "event_type": "RETURNED",
                    "event_date": return_header.return_date.isoformat() if return_header.return_date else None,
                    "ref_type": "SALES_RETURN",
                    "ref_id": str(return_header.id),
                    "ref_no": return_header.return_no,
                    "customer_id": str(return_header.customer_id),
                    "customer_name": customer.customer_name,
                    "notes": return_header.reason,
                    "meta_json": None,
                    "created_at": None,
                }
            )

        timeline_events = sorted(
            synthesized,
            key=lambda event: (event["event_date"] or "", event.get("created_at") or ""),
        )

    payload = {
        "item_code": item_code,
        "serial_number": serial_number,
        "events": timeline_events,
    }

    return jsonify(payload)


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


@bp.get("/invoices/lookup")
@jwt_required()
def lookup_exsol_invoices():
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    query_text = (request.args.get("q") or "").strip()
    if not query_text:
        return jsonify([])

    company_id = _get_exsol_company_id()
    if not company_id:
        return _build_error("Exsol company not configured.", 500)

    invoices = (
        db.session.query(ExsolSalesInvoice, NonSamproxCustomer)
        .join(NonSamproxCustomer, NonSamproxCustomer.id == ExsolSalesInvoice.customer_id)
        .filter(ExsolSalesInvoice.company_key == EXSOL_COMPANY_KEY)
        .filter(NonSamproxCustomer.company_id == company_id)
        .filter(
            or_(
                ExsolSalesInvoice.invoice_no.ilike(f"%{query_text}%"),
                NonSamproxCustomer.customer_name.ilike(f"%{query_text}%"),
            )
        )
        .order_by(ExsolSalesInvoice.invoice_date.desc(), ExsolSalesInvoice.invoice_no.desc())
        .limit(25)
        .all()
    )

    return jsonify(
        [
            {
                "id": str(invoice.id),
                "invoice_no": invoice.invoice_no,
                "invoice_date": invoice.invoice_date.isoformat(),
                "customer_name": customer.customer_name,
            }
            for invoice, customer in invoices
        ]
    )


@bp.get("/invoices/<string:invoice_id>/returnable")
@jwt_required()
def get_returnable_invoice(invoice_id: str):
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    invoice = ExsolSalesInvoice.query.get(invoice_id)
    if not invoice or invoice.company_key != EXSOL_COMPANY_KEY:
        return _build_error("Invoice not found.", 404)

    company_id = _get_exsol_company_id()
    if not company_id:
        return _build_error("Exsol company not configured.", 500)

    customer = invoice.customer
    if customer and customer.company_id != company_id:
        return _build_error("Invoice not found.", 404)

    line_rows = (
        db.session.query(ExsolSalesInvoiceLine, ExsolInventoryItem)
        .join(ExsolInventoryItem, ExsolInventoryItem.id == ExsolSalesInvoiceLine.item_id)
        .filter(ExsolSalesInvoiceLine.invoice_id == invoice.id)
        .order_by(ExsolSalesInvoiceLine.id.asc())
        .all()
    )

    serial_rows = (
        ExsolSalesInvoiceSerial.query.filter(ExsolSalesInvoiceSerial.invoice_id == invoice.id)
        .order_by(ExsolSalesInvoiceSerial.serial_no.asc())
        .all()
    )
    serials_by_line: dict[str, list[ExsolSalesInvoiceSerial]] = {}
    for serial in serial_rows:
        serials_by_line.setdefault(str(serial.line_id), []).append(serial)

    lines_payload = []
    for line, item in line_rows:
        serials = serials_by_line.get(str(line.id), [])
        sold_serials = [row.serial_no for row in serials]
        returned_serials = [row.serial_no for row in serials if (row.status or "").upper() == "RETURNED"]
        lines_payload.append(
            {
                "line_id": str(line.id),
                "item_code": item.item_code,
                "item_name": item.item_name,
                "qty_sold": line.qty,
                "is_serialized": bool(item.is_serialized),
                "sold_serials": sold_serials,
                "already_returned_serials": returned_serials,
            }
        )

    return jsonify(
        {
            "invoice": {
                "id": str(invoice.id),
                "invoice_no": invoice.invoice_no,
                "invoice_date": invoice.invoice_date.isoformat(),
                "customer_id": str(invoice.customer_id),
                "customer_name": customer.customer_name if customer else None,
            },
            "lines": lines_payload,
        }
    )


@bp.post("/returns")
@jwt_required()
def create_exsol_sales_return():
    if not _has_exsol_sales_access():
        return _build_error("Access denied", 403)

    payload = request.get_json(silent=True) or {}
    invoice_id = (payload.get("invoice_id") or "").strip()
    if not invoice_id:
        return _build_error("Invoice is required.", 400)

    invoice = ExsolSalesInvoice.query.get(invoice_id)
    if not invoice or invoice.company_key != EXSOL_COMPANY_KEY:
        return _build_error("Invoice not found.", 404)

    company_id = _get_exsol_company_id()
    if not company_id:
        return _build_error("Exsol company not configured.", 500)

    customer = invoice.customer
    if customer and customer.company_id != company_id:
        return _build_error("Invoice not found.", 404)

    return_date = _parse_date(payload.get("return_date")) or date.today()
    reason = (payload.get("reason") or "").strip() or None
    lines_payload = payload.get("lines") or []
    if not isinstance(lines_payload, list) or not lines_payload:
        return _build_error("At least one return line is required.", 400)

    current_user_id = get_jwt_identity()
    user = User.query.get(int(current_user_id)) if current_user_id else None
    if not user:
        return _build_error("Unable to resolve the current user.", 401)

    line_rows = (
        db.session.query(ExsolSalesInvoiceLine, ExsolInventoryItem)
        .join(ExsolInventoryItem, ExsolInventoryItem.id == ExsolSalesInvoiceLine.item_id)
        .filter(ExsolSalesInvoiceLine.invoice_id == invoice.id)
        .all()
    )
    line_map = {
        str(line.id): {
            "line": line,
            "item": item,
            "item_code": item.item_code,
            "item_name": item.item_name,
            "is_serialized": bool(item.is_serialized),
            "qty_sold": line.qty,
        }
        for line, item in line_rows
    }
    item_code_qty = {}
    for entry in line_map.values():
        item_code_qty[entry["item_code"]] = item_code_qty.get(entry["item_code"], 0) + entry["qty_sold"]

    invoice_serials = (
        ExsolSalesInvoiceSerial.query.filter(ExsolSalesInvoiceSerial.invoice_id == invoice.id)
        .all()
    )
    serial_map = {row.serial_no: row for row in invoice_serials}

    returned_qty_rows = (
        db.session.query(ExsolSalesReturnLine.item_code, func.coalesce(func.sum(ExsolSalesReturnLine.qty), 0))
        .join(ExsolSalesReturn, ExsolSalesReturn.id == ExsolSalesReturnLine.return_id)
        .filter(ExsolSalesReturn.invoice_id == invoice.id)
        .group_by(ExsolSalesReturnLine.item_code)
        .all()
    )
    returned_qty_map = {row[0]: int(row[1] or 0) for row in returned_qty_rows}

    errors: list[str] = []
    prepared_lines: list[dict[str, Any]] = []
    seen_serials: set[str] = set()
    pending_qty: dict[str, int] = {}

    for idx, line_payload in enumerate(lines_payload, start=1):
        line_id = (line_payload.get("line_id") or "").strip()
        payload_item_code = (line_payload.get("item_code") or "").strip()
        qty = _parse_int(line_payload.get("qty"))
        if qty is None or qty <= 0:
            errors.append(f"line {idx}: quantity must be a positive number.")
            continue

        line_info = line_map.get(line_id) if line_id else None
        if not line_info:
            line_info = next(
                (info for info in line_map.values() if info["item_code"] == payload_item_code),
                None,
            )

        if not line_info:
            errors.append(f"line {idx}: item is not found on the invoice.")
            continue

        item_code = line_info["item_code"]
        item_name = line_info["item_name"]
        is_serialized = line_info["is_serialized"]
        qty_sold = item_code_qty.get(item_code, 0)
        qty_returned = returned_qty_map.get(item_code, 0)
        already_pending = pending_qty.get(item_code, 0)
        available_qty = max(qty_sold - qty_returned - already_pending, 0)
        if qty > available_qty:
            errors.append(
                f"line {idx}: return qty exceeds available quantity ({available_qty})."
            )

        pending_qty[item_code] = already_pending + qty

        serials_payload = line_payload.get("serials") or []
        serials_list: list[dict[str, str]] = []
        if is_serialized:
            if not isinstance(serials_payload, list) or not serials_payload:
                errors.append(f"line {idx}: serials are required for serialized items.")
            else:
                for serial_payload in serials_payload:
                    serial_number = (serial_payload.get("serial_number") or "").strip()
                    if not serial_number:
                        continue
                    condition = (serial_payload.get("condition") or "GOOD").strip().upper()
                    restock_status = (serial_payload.get("restock_status") or "STORED").strip().upper()
                    if serial_number in seen_serials:
                        errors.append(f"line {idx}: duplicate serial {serial_number}.")
                    seen_serials.add(serial_number)
                    serials_list.append(
                        {
                            "serial_number": serial_number,
                            "condition": condition or "GOOD",
                            "restock_status": restock_status or "STORED",
                        }
                    )

                if qty != len(serials_list):
                    errors.append(
                        f"line {idx}: quantity must match selected serial count ({len(serials_list)})."
                    )

                for serial_entry in serials_list:
                    serial_number = serial_entry["serial_number"]
                    serial_row = serial_map.get(serial_number)
                    if not serial_row:
                        errors.append(f"line {idx}: serial {serial_number} not found on invoice.")
                        continue
                    if (serial_row.status or "").upper() == "RETURNED":
                        errors.append(f"line {idx}: serial {serial_number} already returned.")
                    if line_id and str(serial_row.line_id) != line_id:
                        errors.append(f"line {idx}: serial {serial_number} does not match the invoice line.")
        else:
            serials_list = []

        prepared_lines.append(
            {
                "item_code": item_code,
                "item_name": item_name,
                "qty": qty,
                "is_serialized": is_serialized,
                "serials": serials_list,
            }
        )

    if errors:
        return _build_error("Validation failed.", 400, errors)

    serials_to_update = [serial["serial_number"] for line in prepared_lines for serial in line["serials"]]
    serials_to_restock = [
        serial["serial_number"]
        for line in prepared_lines
        for serial in line["serials"]
        if serial["condition"] == "GOOD" and serial["restock_status"] == "STORED"
    ]

    try:
        session = db.session
        real_session = session if hasattr(session, "in_transaction") else session()
        transaction_ctx = (
            real_session.begin_nested()
            if real_session.in_transaction()
            else real_session.begin()
        )
        with transaction_ctx:
            return_no = _generate_sales_return_no(company_id=company_id, session=real_session)
            return_header = ExsolSalesReturn(
                company_id=company_id,
                company_key=EXSOL_COMPANY_KEY,
                return_no=return_no,
                invoice_id=invoice.id,
                customer_id=invoice.customer_id,
                return_date=return_date,
                reason=reason,
                status="SUBMITTED",
                created_by_user_id=user.id,
            )
            real_session.add(return_header)

            return_lines = []
            for line in prepared_lines:
                line_model = ExsolSalesReturnLine(
                    return_header=return_header,
                    item_code=line["item_code"],
                    item_name=line["item_name"],
                    qty=line["qty"],
                    is_serialized=line["is_serialized"],
                )
                real_session.add(line_model)
                return_lines.append(line_model)

                for serial in line["serials"]:
                    serial_model = ExsolSalesReturnSerial(
                        return_line=line_model,
                        serial_number=serial["serial_number"],
                        condition=serial["condition"],
                        restock_status=serial["restock_status"],
                    )
                    real_session.add(serial_model)

            if serials_to_update:
                serial_rows = (
                    real_session.query(ExsolSalesInvoiceSerial)
                    .filter(ExsolSalesInvoiceSerial.invoice_id == invoice.id)
                    .filter(ExsolSalesInvoiceSerial.serial_no.in_(serials_to_update))
                    .all()
                )
                for serial in serial_rows:
                    serial.status = "RETURNED"

            if serials_to_restock:
                production_rows = (
                    real_session.query(ExsolProductionSerial)
                    .filter(ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY)
                    .filter(ExsolProductionSerial.serial_no.in_(serials_to_restock))
                    .all()
                )
                for serial in production_rows:
                    serial.is_sold = False

        real_session.commit()

    except IntegrityError:
        db.session.rollback()
        return _build_error("Unable to save return because of a duplicate number.", 409)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            {
                "event": "exsol_sales_return_create_failed",
                "company_key": EXSOL_COMPANY_KEY,
                "invoice_id": str(invoice.id),
                "user_id": user.id,
            }
        )
        return _build_error("Unable to save the return right now.", 500)

    return jsonify({"ok": True, "return_id": str(return_header.id), "return_no": return_header.return_no}), 201
