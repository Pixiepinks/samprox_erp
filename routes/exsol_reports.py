from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy import case, func, or_

from extensions import db
from models import (
    Company,
    ExsolSalesInvoice,
    ExsolSalesInvoiceLine,
    ExsolSalesReceipt,
    NonSamproxCustomer,
    RoleEnum,
    User,
    normalize_role,
)

bp = Blueprint("exsol_reports", __name__, url_prefix="/api/exsol/reports")

EXSOL_COMPANY_NAME = "Exsol Engineering (Pvt) Ltd"
EXSOL_COMPANY_KEY = "EXSOL"


def _has_exsol_sales_access() -> bool:
    try:
        claims = get_jwt()
    except Exception:
        return False

    role = normalize_role(claims.get("role"))
    company_key = (claims.get("company_key") or claims.get("company") or "").strip().lower()

    if role not in {RoleEnum.sales_manager, RoleEnum.sales_executive, RoleEnum.admin}:
        return False

    if role != RoleEnum.admin and company_key and company_key != "exsol-engineering":
        return False

    return True


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


def _parse_int(value: Any, *, min_value: int | None = None) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if min_value is not None and parsed < min_value:
        return None
    return parsed


def _normalize_status(value: str | None) -> str | None:
    if not value:
        return None
    normalized = " ".join(str(value).strip().lower().replace("_", " ").split())
    if not normalized or normalized == "all":
        return None
    if normalized in {"partially paid", "partial paid", "partially", "partial"}:
        return "Partially Paid"
    if normalized in {"unpaid"}:
        return "Unpaid"
    if normalized in {"paid"}:
        return "Paid"
    if normalized in {"draft"}:
        return "Draft"
    if normalized in {"cancelled", "canceled"}:
        return "Cancelled"
    return value.strip()


def _quantize_money(value: Decimal | None) -> Decimal:
    if value is None:
        value = Decimal("0")
    elif not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money_to_float(value: Decimal | None) -> float:
    return float(_quantize_money(value))


def _build_failure_log(filters: dict[str, Any]) -> dict[str, Any]:
    claims = None
    try:
        claims = get_jwt()
    except Exception:
        claims = None

    user_id = None
    try:
        identity = get_jwt_identity()
        user_id = int(identity) if identity else None
    except (TypeError, ValueError):
        user_id = None

    role = normalize_role(claims.get("role")) if claims else None
    company_key = (claims.get("company_key") or claims.get("company")) if claims else None

    return {
        "event": "exsol_sales_report_invoices_failure",
        "user_id": user_id,
        "role": role.value if role else None,
        "company_key": company_key,
        "filters": filters,
    }


def _build_report_query(company_id: int):
    receipt_agg = (
        db.session.query(
            ExsolSalesReceipt.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(ExsolSalesReceipt.amount), 0).label("paid"),
            func.max(ExsolSalesReceipt.receipt_date).label("last_payment_date"),
        )
        .filter(ExsolSalesReceipt.company_key == EXSOL_COMPANY_KEY)
        .group_by(ExsolSalesReceipt.invoice_id)
        .subquery()
    )

    line_agg = (
        db.session.query(
            ExsolSalesInvoiceLine.invoice_id.label("invoice_id"),
            func.count(ExsolSalesInvoiceLine.id).label("items_count"),
        )
        .group_by(ExsolSalesInvoiceLine.invoice_id)
        .subquery()
    )

    paid_col = func.coalesce(receipt_agg.c.paid, 0)
    due_col = case(
        (ExsolSalesInvoice.grand_total - paid_col <= 0, 0),
        else_=ExsolSalesInvoice.grand_total - paid_col,
    )
    payment_status_col = case(
        (ExsolSalesInvoice.grand_total <= 0, "N/A"),
        (paid_col <= 0, "Unpaid"),
        (paid_col >= ExsolSalesInvoice.grand_total, "Paid"),
        else_="Partially Paid",
    )
    status_col = func.coalesce(ExsolSalesInvoice.status, payment_status_col)

    query = (
        db.session.query(
            ExsolSalesInvoice.id.label("invoice_id"),
            ExsolSalesInvoice.invoice_no,
            ExsolSalesInvoice.invoice_date,
            ExsolSalesInvoice.customer_id,
            ExsolSalesInvoice.sales_rep_id,
            ExsolSalesInvoice.grand_total.label("total"),
            paid_col.label("paid"),
            due_col.label("due"),
            status_col.label("status"),
            payment_status_col.label("payment_status"),
            receipt_agg.c.last_payment_date,
            line_agg.c.items_count,
            NonSamproxCustomer.customer_name,
            NonSamproxCustomer.city,
            User.name.label("sales_rep"),
        )
        .join(NonSamproxCustomer, NonSamproxCustomer.id == ExsolSalesInvoice.customer_id)
        .outerjoin(User, User.id == ExsolSalesInvoice.sales_rep_id)
        .outerjoin(receipt_agg, receipt_agg.c.invoice_id == ExsolSalesInvoice.id)
        .outerjoin(line_agg, line_agg.c.invoice_id == ExsolSalesInvoice.id)
        .filter(ExsolSalesInvoice.company_key == EXSOL_COMPANY_KEY)
        .filter(NonSamproxCustomer.company_id == company_id)
    )

    return query, status_col, payment_status_col, paid_col, due_col


def _apply_filters(query, *, status_col, filters: dict[str, Any]):
    date_from = _parse_date(filters.get("date_from"))
    date_to = _parse_date(filters.get("date_to"))
    customer_id = (filters.get("customer_id") or "").strip()
    status = _normalize_status(filters.get("status"))
    query_text = (filters.get("q") or "").strip()
    min_total = _parse_decimal(filters.get("min_total"))
    max_total = _parse_decimal(filters.get("max_total"))

    if date_from:
        query = query.filter(ExsolSalesInvoice.invoice_date >= date_from)
    if date_to:
        query = query.filter(ExsolSalesInvoice.invoice_date <= date_to)
    if customer_id:
        query = query.filter(ExsolSalesInvoice.customer_id == customer_id)
    if status:
        query = query.filter(func.lower(status_col) == status.lower())
    if query_text:
        like_value = f"%{query_text}%"
        query = query.filter(
            or_(
                ExsolSalesInvoice.invoice_no.ilike(like_value),
                NonSamproxCustomer.customer_name.ilike(like_value),
                NonSamproxCustomer.area_code.ilike(like_value),
                NonSamproxCustomer.city.ilike(like_value),
            )
        )
    if min_total is not None:
        query = query.filter(ExsolSalesInvoice.grand_total >= min_total)
    if max_total is not None:
        query = query.filter(ExsolSalesInvoice.grand_total <= max_total)

    return query


def _serialize_filters_for_log(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "date_from": args.get("date_from"),
        "date_to": args.get("date_to"),
        "customer_id": args.get("customer_id"),
        "status": args.get("status"),
        "q_length": len(args.get("q") or ""),
        "min_total": args.get("min_total"),
        "max_total": args.get("max_total"),
        "page": args.get("page"),
        "page_size": args.get("page_size"),
        "sort_by": args.get("sort_by"),
        "sort_dir": args.get("sort_dir"),
    }


def _resolve_sort(sort_by: str | None, sort_dir: str | None, *, due_col):
    sort_by_value = (sort_by or "date").strip().lower()
    sort_dir_value = (sort_dir or "desc").strip().lower()

    sort_map = {
        "date": ExsolSalesInvoice.invoice_date,
        "invoice_no": ExsolSalesInvoice.invoice_no,
        "customer": NonSamproxCustomer.customer_name,
        "total": ExsolSalesInvoice.grand_total,
        "due": due_col,
    }
    sort_col = sort_map.get(sort_by_value, ExsolSalesInvoice.invoice_date)
    direction = sort_col.asc() if sort_dir_value == "asc" else sort_col.desc()
    return direction


def _build_kpis(filtered_subquery):
    status_lower = func.lower(filtered_subquery.c.status)
    return (
        db.session.query(
            func.count(filtered_subquery.c.invoice_id).label("invoice_count"),
            func.coalesce(func.sum(filtered_subquery.c.total), 0).label("gross_sales"),
            func.coalesce(func.sum(filtered_subquery.c.paid), 0).label("paid"),
            func.coalesce(func.sum(filtered_subquery.c.due), 0).label("due"),
            func.coalesce(func.sum(case((status_lower == "draft", 1), else_=0)), 0).label(
                "draft_count"
            ),
            func.coalesce(func.sum(case((status_lower == "unpaid", 1), else_=0)), 0).label(
                "unpaid_count"
            ),
            func.coalesce(
                func.sum(case((status_lower == "partially paid", 1), else_=0)), 0
            ).label("partial_count"),
            func.coalesce(func.sum(case((status_lower == "paid", 1), else_=0)), 0).label(
                "paid_count"
            ),
            func.coalesce(
                func.sum(case((status_lower == "cancelled", 1), else_=0)), 0
            ).label("cancelled_count"),
        )
        .one()
    )


@bp.get("/sales/invoices")
@jwt_required()
def exsol_sales_invoice_report():
    if not _has_exsol_sales_access():
        return jsonify({"ok": False, "error": "Access denied"}), 403

    args = request.args.to_dict(flat=True)

    try:
        company_id = _get_exsol_company_id()
        if not company_id:
            return jsonify({"ok": False, "error": "Exsol company not configured"}), 500

        base_query, status_col, _payment_status_col, _paid_col, due_col = _build_report_query(
            company_id
        )
        filtered_query = _apply_filters(base_query, status_col=status_col, filters=args)

        filtered_subquery = filtered_query.subquery()
        total_rows = db.session.query(func.count()).select_from(filtered_subquery).scalar() or 0

        page = _parse_int(args.get("page"), min_value=1) or 1
        page_size = _parse_int(args.get("page_size"), min_value=1) or 25
        if page_size > 200:
            page_size = 200
        total_pages = max((total_rows + page_size - 1) // page_size, 1)
        if page > total_pages:
            page = total_pages

        order_by = _resolve_sort(args.get("sort_by"), args.get("sort_dir"), due_col=due_col)

        rows = (
            filtered_query.order_by(order_by)
            .limit(page_size)
            .offset((page - 1) * page_size)
            .all()
        )

        kpis_row = _build_kpis(filtered_subquery)

        payload_rows = []
        for row in rows:
            payload_rows.append(
                {
                    "invoice_id": str(row.invoice_id),
                    "invoice_no": row.invoice_no,
                    "invoice_date": row.invoice_date.isoformat() if row.invoice_date else None,
                    "customer_id": str(row.customer_id),
                    "customer_name": row.customer_name,
                    "customer_city": row.city,
                    "sales_rep": row.sales_rep,
                    "total": _money_to_float(row.total),
                    "paid": _money_to_float(row.paid),
                    "due": _money_to_float(row.due),
                    "status": row.status,
                    "payment_status": row.payment_status,
                    "items_count": int(row.items_count or 0),
                    "last_payment_date": row.last_payment_date.isoformat()
                    if row.last_payment_date
                    else None,
                }
            )

        return jsonify(
            {
                "kpis": {
                    "invoice_count": int(kpis_row.invoice_count or 0),
                    "gross_sales": _money_to_float(kpis_row.gross_sales),
                    "paid": _money_to_float(kpis_row.paid),
                    "due": _money_to_float(kpis_row.due),
                    "by_status": {
                        "Draft": int(kpis_row.draft_count or 0),
                        "Unpaid": int(kpis_row.unpaid_count or 0),
                        "Partially Paid": int(kpis_row.partial_count or 0),
                        "Paid": int(kpis_row.paid_count or 0),
                        "Cancelled": int(kpis_row.cancelled_count or 0),
                    },
                },
                "rows": payload_rows,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_rows": total_rows,
                    "total_pages": total_pages,
                },
            }
        )
    except Exception:
        current_app.logger.exception(_build_failure_log(_serialize_filters_for_log(args)))
        return jsonify({"ok": False, "error": "Unable to load report"}), 500


@bp.get("/sales/invoices/export.csv")
@jwt_required()
def export_exsol_sales_invoice_report_csv():
    if not _has_exsol_sales_access():
        return jsonify({"ok": False, "error": "Access denied"}), 403

    args = request.args.to_dict(flat=True)

    try:
        company_id = _get_exsol_company_id()
        if not company_id:
            return jsonify({"ok": False, "error": "Exsol company not configured"}), 500

        base_query, status_col, _payment_status_col, _paid_col, due_col = _build_report_query(
            company_id
        )
        filtered_query = _apply_filters(base_query, status_col=status_col, filters=args)
        order_by = _resolve_sort(args.get("sort_by"), args.get("sort_dir"), due_col=due_col)
        rows = filtered_query.order_by(order_by).all()

        def generate():
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(
                [
                    "Invoice No",
                    "Date",
                    "Customer",
                    "City",
                    "Total",
                    "Paid",
                    "Due",
                    "Status",
                    "Sales Rep",
                ]
            )
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

            for row in rows:
                writer.writerow(
                    [
                        row.invoice_no,
                        row.invoice_date.isoformat() if row.invoice_date else "",
                        row.customer_name,
                        row.city or "",
                        f"{_quantize_money(row.total):.2f}",
                        f"{_quantize_money(row.paid):.2f}",
                        f"{_quantize_money(row.due):.2f}",
                        row.status or "",
                        row.sales_rep or "",
                    ]
                )
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

        headers = {"Content-Disposition": "attachment; filename=exsol_sales_invoices.csv"}
        return Response(generate(), mimetype="text/csv", headers=headers)
    except Exception:
        current_app.logger.exception(_build_failure_log(_serialize_filters_for_log(args)))
        return jsonify({"ok": False, "error": "Unable to export report"}), 500
