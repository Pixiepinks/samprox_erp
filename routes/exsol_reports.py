from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request
from flask_jwt_extended import (
    get_jwt,
    get_jwt_identity,
    jwt_required,
    verify_jwt_in_request,
)
from sqlalchemy import String, case, cast, func, or_

from extensions import db
from models import (
    Company,
    ExsolInventoryItem,
    ExsolProductionEntry,
    ExsolProductionSerial,
    ExsolSalesInvoice,
    ExsolSalesInvoiceLine,
    ExsolSalesInvoiceSerial,
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
                if role is None:
                    role = user.role
                if not company_key:
                    company_key = (user.company_key or "").strip().lower()

    allowed_roles = {RoleEnum.sales_manager, RoleEnum.sales_executive, RoleEnum.admin}
    if role not in allowed_roles:
        return False

    if role == RoleEnum.admin:
        return True

    return company_key == "exsol-engineering"


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
    """Parse a decimal value from request filters."""
    if value in {None, ""}:
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


def _resolve_date_range(args: dict[str, Any]) -> tuple[date, date]:
    date_from = _parse_date(args.get("date_from"))
    date_to = _parse_date(args.get("date_to"))

    today = date.today()
    if not date_from:
        date_from = today.replace(day=1)
    if not date_to:
        date_to = today

    return date_from, date_to


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


def _clean_geo_value(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or None


def _dedupe_geo_values(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        cleaned = _clean_geo_value(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


@bp.get("/geo-options")
@jwt_required()
def exsol_sales_geo_options():
    if not _has_exsol_sales_access():
        return jsonify({"ok": False, "error": "Access denied"}), 403

    args = request.args.to_dict(flat=True)
    province = _clean_geo_value(args.get("province"))

    try:
        company_id = _get_exsol_company_id()
        if not company_id:
            return jsonify({"ok": False, "error": "Exsol company not configured"}), 500

        base_filter = [NonSamproxCustomer.company_id == company_id]

        provinces = (
            db.session.query(func.distinct(NonSamproxCustomer.province))
            .filter(*base_filter)
            .filter(NonSamproxCustomer.province.isnot(None))
            .filter(NonSamproxCustomer.province != "")
            .order_by(func.lower(NonSamproxCustomer.province))
            .all()
        )
        province_values = _dedupe_geo_values([row[0] for row in provinces])

        district_filters = list(base_filter)
        if province:
            district_filters.append(func.lower(NonSamproxCustomer.province) == province.lower())

        districts = (
            db.session.query(func.distinct(NonSamproxCustomer.district))
            .filter(*district_filters)
            .filter(NonSamproxCustomer.district.isnot(None))
            .filter(NonSamproxCustomer.district != "")
            .order_by(func.lower(NonSamproxCustomer.district))
            .all()
        )
        district_values = _dedupe_geo_values([row[0] for row in districts])

        return jsonify({"provinces": province_values, "districts": district_values})
    except Exception:
        current_app.logger.exception("Unable to load exsol geo options")
        return jsonify({"ok": False, "error": "Unable to load geo options"}), 500


@bp.get("/sales-by-person")
@jwt_required()
def exsol_sales_by_person_report():
    if not _has_exsol_sales_access():
        return jsonify({"ok": False, "error": "Access denied"}), 403

    args = request.args.to_dict(flat=True)
    province = _clean_geo_value(args.get("province")) or ""
    district = _clean_geo_value(args.get("district")) or ""

    try:
        company_id = _get_exsol_company_id()
        if not company_id:
            return jsonify({"ok": False, "error": "Exsol company not configured"}), 500

        date_from, date_to = _resolve_date_range(args)

        sales_person_name = func.coalesce(
            User.name, cast(ExsolSalesInvoice.sales_rep_id, String)
        ).label("sales_person")

        query = (
            db.session.query(
                ExsolSalesInvoice.sales_rep_id.label("sales_person_id"),
                sales_person_name,
                NonSamproxCustomer.customer_name.label("customer_name"),
                func.count(ExsolSalesInvoice.id).label("invoice_count"),
                func.coalesce(func.sum(ExsolSalesInvoice.grand_total), 0).label("net_total"),
            )
            .join(NonSamproxCustomer, NonSamproxCustomer.id == ExsolSalesInvoice.customer_id)
            .outerjoin(User, User.id == ExsolSalesInvoice.sales_rep_id)
            .filter(ExsolSalesInvoice.company_key == EXSOL_COMPANY_KEY)
            .filter(NonSamproxCustomer.company_id == company_id)
            .filter(ExsolSalesInvoice.invoice_date >= date_from)
            .filter(ExsolSalesInvoice.invoice_date <= date_to)
        )

        if province:
            query = query.filter(func.lower(NonSamproxCustomer.province) == province.lower())
        if district:
            query = query.filter(func.lower(NonSamproxCustomer.district) == district.lower())

        rows = (
            query.group_by(
                ExsolSalesInvoice.sales_rep_id,
                sales_person_name,
                NonSamproxCustomer.customer_name,
            )
            .order_by(func.sum(ExsolSalesInvoice.grand_total).desc())
            .all()
        )

        payload_rows = []
        total_invoices = 0
        total_net = Decimal("0")

        for row in rows:
            invoice_count = int(row.invoice_count or 0)
            net_total = _quantize_money(row.net_total)
            total_invoices += invoice_count
            total_net += net_total

            payload_rows.append(
                {
                    "sales_person_id": int(row.sales_person_id) if row.sales_person_id else None,
                    "sales_person": row.sales_person,
                    "customer_name": row.customer_name,
                    "invoice_count": invoice_count,
                    "net_total": _money_to_float(net_total),
                }
            )

        return jsonify(
            {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "filters": {"province": province, "district": district},
                "rows": payload_rows,
                "totals": {
                    "invoice_count": total_invoices,
                    "net_total": _money_to_float(total_net),
                },
            }
        )
    except Exception:
        current_app.logger.exception("Unable to load exsol sales by person report")
        return jsonify({"ok": False, "error": "Unable to load report"}), 500


@bp.get("/sales-by-person.csv")
@jwt_required()
def exsol_sales_by_person_report_csv():
    if not _has_exsol_sales_access():
        return jsonify({"ok": False, "error": "Access denied"}), 403

    args = request.args.to_dict(flat=True)
    province = _clean_geo_value(args.get("province")) or ""
    district = _clean_geo_value(args.get("district")) or ""

    try:
        company_id = _get_exsol_company_id()
        if not company_id:
            return jsonify({"ok": False, "error": "Exsol company not configured"}), 500

        date_from, date_to = _resolve_date_range(args)

        sales_person_name = func.coalesce(
            User.name, cast(ExsolSalesInvoice.sales_rep_id, String)
        ).label("sales_person")

        query = (
            db.session.query(
                ExsolSalesInvoice.sales_rep_id.label("sales_person_id"),
                sales_person_name,
                NonSamproxCustomer.customer_name.label("customer_name"),
                func.count(ExsolSalesInvoice.id).label("invoice_count"),
                func.coalesce(func.sum(ExsolSalesInvoice.grand_total), 0).label("net_total"),
            )
            .join(NonSamproxCustomer, NonSamproxCustomer.id == ExsolSalesInvoice.customer_id)
            .outerjoin(User, User.id == ExsolSalesInvoice.sales_rep_id)
            .filter(ExsolSalesInvoice.company_key == EXSOL_COMPANY_KEY)
            .filter(NonSamproxCustomer.company_id == company_id)
            .filter(ExsolSalesInvoice.invoice_date >= date_from)
            .filter(ExsolSalesInvoice.invoice_date <= date_to)
        )

        if province:
            query = query.filter(func.lower(NonSamproxCustomer.province) == province.lower())
        if district:
            query = query.filter(func.lower(NonSamproxCustomer.district) == district.lower())

        rows = (
            query.group_by(
                ExsolSalesInvoice.sales_rep_id,
                sales_person_name,
                NonSamproxCustomer.customer_name,
            )
            .order_by(func.sum(ExsolSalesInvoice.grand_total).desc())
            .all()
        )

        def generate():
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Sales Person", "Customer Name", "Invoice Count", "Net Total"])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

            for row in rows:
                writer.writerow(
                    [
                        row.sales_person,
                        row.customer_name,
                        int(row.invoice_count or 0),
                        f"{_quantize_money(row.net_total):.2f}",
                    ]
                )
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

        headers = {"Content-Disposition": "attachment; filename=exsol_sales_by_person.csv"}
        response = Response(generate(), mimetype="text/csv", headers=headers)
        return response
    except Exception:
        current_app.logger.exception("Unable to export exsol sales by person report")
        return jsonify({"ok": False, "error": "Unable to export report"}), 500


@bp.get("/item-serials")
@jwt_required(optional=True)
def exsol_item_serials_report():
    if not _has_exsol_sales_access():
        return jsonify({"ok": False, "error": "Access denied"}), 403

    item_code = (request.args.get("item_code") or "").strip()
    if not item_code:
        return jsonify({"ok": False, "error": "Item code is required."}), 400

    company_id = _get_exsol_company_id()
    if not company_id:
        return jsonify({"ok": False, "error": "Exsol company not configured."}), 500

    search = (request.args.get("search") or "").strip()
    like = f"%{search}%"

    stored_query = (
        db.session.query(
            ExsolProductionSerial.id.label("record_id"),
            ExsolProductionSerial.serial_no.label("serial_number"),
            ExsolProductionEntry.item_code.label("item_code"),
            ExsolProductionEntry.item_name.label("item_name"),
        )
        .join(ExsolProductionEntry, ExsolProductionSerial.entry_id == ExsolProductionEntry.id)
        .filter(ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolProductionEntry.item_code == item_code)
        .filter(ExsolProductionSerial.is_sold.is_(False))
    )

    if search:
        stored_query = stored_query.filter(
            or_(
                ExsolProductionSerial.serial_no.ilike(like),
                ExsolProductionEntry.item_name.ilike(like),
                ExsolProductionEntry.item_code.ilike(like),
            )
        )

    sold_query = (
        db.session.query(
            ExsolSalesInvoiceSerial.id.label("record_id"),
            ExsolSalesInvoiceSerial.serial_no.label("serial_number"),
            ExsolInventoryItem.item_code.label("item_code"),
            ExsolInventoryItem.item_name.label("item_name"),
        )
        .join(ExsolInventoryItem, ExsolSalesInvoiceSerial.item_id == ExsolInventoryItem.id)
        .filter(ExsolSalesInvoiceSerial.company_key == EXSOL_COMPANY_KEY)
        .filter(ExsolInventoryItem.company_id == company_id)
        .filter(ExsolInventoryItem.item_code == item_code)
    )

    if search:
        sold_query = sold_query.filter(
            or_(
                ExsolSalesInvoiceSerial.serial_no.ilike(like),
                ExsolInventoryItem.item_name.ilike(like),
                ExsolInventoryItem.item_code.ilike(like),
            )
        )

    stored_rows = stored_query.order_by(ExsolProductionSerial.serial_no.asc()).all()
    sold_rows = sold_query.order_by(ExsolSalesInvoiceSerial.serial_no.asc()).all()

    payload = [
        {
            "record_id": str(row.record_id),
            "record_type": "stored",
            "item_code": row.item_code,
            "item_name": row.item_name,
            "serial_number": row.serial_number,
            "status": "Stored",
        }
        for row in stored_rows
    ]
    payload.extend(
        [
            {
                "record_id": str(row.record_id),
                "record_type": "sold",
                "item_code": row.item_code,
                "item_name": row.item_name,
                "serial_number": row.serial_number,
                "status": "Sold",
            }
            for row in sold_rows
        ]
    )

    return jsonify({"ok": True, "data": payload})


@bp.patch("/item-serials")
@jwt_required()
def update_exsol_item_serial():
    if not _has_exsol_sales_access():
        return jsonify({"ok": False, "error": "Access denied"}), 403

    payload = request.get_json(silent=True) or {}
    record_id = payload.get("record_id")
    record_type = (payload.get("record_type") or "").strip().lower()
    serial_number = (payload.get("serial_number") or "").strip()
    item_code = (payload.get("item_code") or "").strip()

    if not record_id or record_type not in {"stored", "sold"}:
        return jsonify({"ok": False, "error": "Record details are required."}), 400

    if not serial_number:
        return jsonify({"ok": False, "error": "Serial number is required."}), 400

    if not item_code:
        return jsonify({"ok": False, "error": "Item code is required."}), 400

    company_id = _get_exsol_company_id()
    if not company_id:
        return jsonify({"ok": False, "error": "Exsol company not configured."}), 500

    item = (
        ExsolInventoryItem.query.filter(
            ExsolInventoryItem.company_id == company_id,
            ExsolInventoryItem.item_code == item_code,
        )
        .one_or_none()
    )
    if not item:
        return jsonify({"ok": False, "error": "Item code not found."}), 400

    entry = (
        ExsolProductionEntry.query.filter(
            ExsolProductionEntry.company_key == EXSOL_COMPANY_KEY,
            ExsolProductionEntry.item_code == item_code,
        )
        .order_by(ExsolProductionEntry.production_date.desc(), ExsolProductionEntry.id.desc())
        .first()
    )

    try:
        if record_type == "stored":
            try:
                record_id_int = int(record_id)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "Invalid record id."}), 400

            serial = (
                ExsolProductionSerial.query.filter(
                    ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY,
                    ExsolProductionSerial.id == record_id_int,
                )
                .one_or_none()
            )
            if not serial:
                return jsonify({"ok": False, "error": "Serial not found."}), 404
            if not entry:
                return jsonify(
                    {"ok": False, "error": "No production entry found for the selected item."}
                ), 400

            if serial.serial_no != serial_number:
                duplicate_production = (
                    ExsolProductionSerial.query.filter(
                        ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY,
                        ExsolProductionSerial.serial_no == serial_number,
                        ExsolProductionSerial.id != serial.id,
                    )
                    .first()
                )
                duplicate_sales = (
                    ExsolSalesInvoiceSerial.query.filter(
                        ExsolSalesInvoiceSerial.company_key == EXSOL_COMPANY_KEY,
                        ExsolSalesInvoiceSerial.serial_no == serial_number,
                    )
                    .first()
                )
                if duplicate_production or duplicate_sales:
                    return jsonify({"ok": False, "error": "Serial number already exists."}), 409

            serial.serial_no = serial_number
            serial.entry_id = entry.id
        else:
            serial = (
                ExsolSalesInvoiceSerial.query.filter(
                    ExsolSalesInvoiceSerial.company_key == EXSOL_COMPANY_KEY,
                    ExsolSalesInvoiceSerial.id == record_id,
                )
                .one_or_none()
            )
            if not serial:
                return jsonify({"ok": False, "error": "Serial not found."}), 404

            old_serial = serial.serial_no
            if serial.serial_no != serial_number:
                duplicate_production = (
                    ExsolProductionSerial.query.filter(
                        ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY,
                        ExsolProductionSerial.serial_no == serial_number,
                    )
                    .first()
                )
                duplicate_sales = (
                    ExsolSalesInvoiceSerial.query.filter(
                        ExsolSalesInvoiceSerial.company_key == EXSOL_COMPANY_KEY,
                        ExsolSalesInvoiceSerial.serial_no == serial_number,
                        ExsolSalesInvoiceSerial.id != serial.id,
                    )
                    .first()
                )
                if duplicate_production or duplicate_sales:
                    return jsonify({"ok": False, "error": "Serial number already exists."}), 409

            production_serial = (
                ExsolProductionSerial.query.filter(
                    ExsolProductionSerial.company_key == EXSOL_COMPANY_KEY,
                    ExsolProductionSerial.serial_no == old_serial,
                )
                .one_or_none()
            )
            if production_serial and not entry:
                return jsonify(
                    {"ok": False, "error": "No production entry found for the selected item."}
                ), 400

            serial.serial_no = serial_number
            serial.item_id = item.id

            if production_serial:
                production_serial.serial_no = serial_number
                production_serial.entry_id = entry.id

        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Unable to update Exsol item serial")
        return jsonify({"ok": False, "error": "Unable to update serial right now."}), 500

    return jsonify({"ok": True})
