from __future__ import annotations

import base64
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, jsonify, render_template, send_file
from flask_jwt_extended import jwt_required
from sqlalchemy.orm import joinedload
from xhtml2pdf import pisa

from models import (
    MaintenanceInternalStaffCost,
    MaintenanceJob,
    MaintenanceOutsourcedService,
)

bp = Blueprint(
    "maintenance_job_documents",
    __name__,
    url_prefix="/machines/maintenance-jobs",
)

_CURRENCY_QUANT = Decimal("0.01")
_QUANTITY_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)")
_COLOMBO_TZ = ZoneInfo("Asia/Colombo")


def _as_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _quantize_currency(value: Decimal) -> Decimal:
    try:
        return value.quantize(_CURRENCY_QUANT)
    except (InvalidOperation, AttributeError):
        return Decimal("0.00")


def _format_currency(value: Decimal | None) -> str:
    amount = _quantize_currency(_as_decimal(value)) if value is not None else Decimal("0")
    return f"Rs. {amount:,.2f}"


def _format_hours(value) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:,.2f}" if number % 1 else f"{int(number)}"


def _format_date(value) -> str:
    if not value:
        return "—"
    try:
        return value.strftime("%d %b %Y")
    except AttributeError:
        return str(value)


def _load_logo_data_uri() -> str | None:
    logo_path = current_app.config.get("COMPANY_LOGO_PATH")
    if logo_path:
        candidate = logo_path
        if not os.path.isabs(candidate):
            candidate = os.path.join(current_app.root_path, candidate)
    else:
        candidate = os.path.join(current_app.static_folder or "", "favicon.ico")

    if not candidate or not os.path.exists(candidate):
        return None

    try:
        with open(candidate, "rb") as logo_file:
            encoded = base64.b64encode(logo_file.read()).decode("utf-8")
        ext = os.path.splitext(candidate)[1].lstrip(".").lower() or "png"
        mime = "image/png"
        if ext in {"ico", "icon"}:
            mime = "image/x-icon"
        elif ext == "jpg":
            mime = "image/jpeg"
        elif ext == "svg":
            mime = "image/svg+xml"
        elif ext == "jpeg":
            mime = "image/jpeg"
        return f"data:{mime};base64,{encoded}"
    except OSError:
        return None


def _materials_context(job: MaintenanceJob) -> tuple[list[dict], Decimal]:
    lines: list[dict] = []
    total = Decimal("0")
    for material in job.materials or []:
        cost = _quantize_currency(_as_decimal(material.cost))
        total += cost
        quantity = None
        unit_cost = None
        units_display = material.units or ""
        if material.units:
            match = _QUANTITY_PATTERN.match(material.units)
            if match:
                try:
                    quantity = Decimal(match.group(1))
                    if quantity:
                        unit_cost = _quantize_currency(cost / quantity)
                except (InvalidOperation, ValueError):
                    quantity = None
                    unit_cost = None
        lines.append(
            {
                "name": material.material_name or "",
                "quantity": quantity,
                "quantity_display": units_display,
                "unit_cost": unit_cost,
                "line_total": cost,
            }
        )
    return lines, total


def _outsourced_context(job: MaintenanceJob) -> tuple[list[dict], Decimal]:
    lines: list[dict] = []
    total = Decimal("0")
    for service in job.outsourced_services or []:
        cost = _quantize_currency(_as_decimal(service.cost))
        total += cost
        supplier = getattr(service, "supplier", None)
        lines.append(
            {
                "party": getattr(supplier, "name", "") or "",
                "service_date": service.service_date,
                "description": service.service_description or "",
                "hours": service.engaged_hours,
                "cost": cost,
            }
        )
    return lines, total


def _internal_staff_context(job: MaintenanceJob) -> tuple[list[dict], Decimal]:
    lines: list[dict] = []
    total = Decimal("0")
    for entry in job.internal_staff_costs or []:
        cost = _quantize_currency(_as_decimal(entry.cost))
        total += cost
        employee = getattr(entry, "employee", None)
        employee_label = ""
        if employee:
            reg_number = getattr(employee, "reg_number", None)
            name = getattr(employee, "name", None)
            if reg_number and name:
                employee_label = f"{reg_number} – {name}"
            else:
                employee_label = name or reg_number or ""
        lines.append(
            {
                "employee": employee_label,
                "service_date": entry.service_date,
                "description": entry.work_description or "",
                "hours": entry.engaged_hours,
                "hourly_rate": _quantize_currency(_as_decimal(entry.hourly_rate)) if entry.hourly_rate is not None else None,
                "cost": cost,
            }
        )
    return lines, total


@bp.get("/<int:job_id>/download-pdf")
@jwt_required()
def download_job_card(job_id: int):
    job = (
        MaintenanceJob.query.options(
            joinedload(MaintenanceJob.asset),
            joinedload(MaintenanceJob.part),
            joinedload(MaintenanceJob.assigned_to),
            joinedload(MaintenanceJob.created_by),
            joinedload(MaintenanceJob.materials),
            joinedload(MaintenanceJob.outsourced_services).joinedload(
                MaintenanceOutsourcedService.supplier
            ),
            joinedload(MaintenanceJob.internal_staff_costs).joinedload(
                MaintenanceInternalStaffCost.employee
            ),
        )
        .filter(MaintenanceJob.id == job_id)
        .first()
    )

    if not job:
        return jsonify({"msg": "Maintenance job not found."}), 404

    materials, materials_total = _materials_context(job)
    outsourced, outsourced_total = _outsourced_context(job)
    internal, internal_total = _internal_staff_context(job)
    overall_total = _quantize_currency(materials_total + outsourced_total + internal_total)

    company_name = current_app.config.get("COMPANY_NAME", "SAMPROX ERP")
    company_address = current_app.config.get("COMPANY_ADDRESS")
    company_contact = current_app.config.get("COMPANY_CONTACT")
    company_tagline = current_app.config.get("COMPANY_TAGLINE")
    logo_data = _load_logo_data_uri()

    generated_at = datetime.now(_COLOMBO_TZ)

    status = job.status
    status_label = getattr(status, "value", status) if status else ""

    html = render_template(
        "maintenance/job_card.html",
        job=job,
        materials=materials,
        outsourced_services=outsourced,
        internal_staff=internal,
        totals={
            "materials": materials_total,
            "outsourced": outsourced_total,
            "internal": internal_total,
            "overall": overall_total,
        },
        company={
            "name": company_name,
            "address": company_address,
            "contact": company_contact,
            "tagline": company_tagline,
            "logo": logo_data,
        },
        generated_at=generated_at,
        format_currency=_format_currency,
        format_date=_format_date,
        format_hours=_format_hours,
        status_label=status_label,
    )

    pdf_buffer = BytesIO()
    pdf_status = pisa.CreatePDF(html, dest=pdf_buffer)

    if pdf_status.err:
        current_app.logger.error(
            "Failed to generate maintenance job card PDF for job %s", job_id
        )
        return (
            jsonify({"msg": "Unable to generate the job card PDF at this time."}),
            500,
        )

    pdf_buffer.seek(0)
    job_code = str(job.job_code or job.id).strip()
    if job_code.upper().startswith("JOB-"):
        filename = f"{job_code}.pdf"
    else:
        filename = f"JOB-{job_code}.pdf"
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
