from __future__ import annotations

import re
import smtplib
import ssl
import socket
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from flask import Blueprint, jsonify, request, url_for, current_app
from flask_jwt_extended import get_jwt, jwt_required
from flask_mail import Message
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from extensions import db, mail
from models import (
    MachineAsset,
    MachinePart,
    MaintenanceJob,
    MaintenanceJobStatus,
    MaintenanceMaterial,
    MaintenanceOutsourcedService,
    MaintenanceInternalStaffCost,
    RoleEnum,
    ServiceSupplier,
    TeamMember,
    User,
)
from schemas import MaintenanceJobSchema

bp = Blueprint("maintenance_jobs", __name__, url_prefix="/api/maintenance-jobs")
job_schema = MaintenanceJobSchema()
jobs_schema = MaintenanceJobSchema(many=True)

_CODE_PATTERN = re.compile(r"(\d+)$")
_ALLOWED_PRIORITIES = {"Normal", "Urgent", "Critical"}


def _current_role() -> Optional[RoleEnum]:
    claims = get_jwt()
    try:
        return RoleEnum(claims.get("role"))
    except (ValueError, TypeError):
        return None


def _current_user_id() -> Optional[int]:
    claims = get_jwt()
    try:
        return int(claims.get("sub"))
    except (TypeError, ValueError):
        return None


def require_role(*roles: RoleEnum) -> bool:
    role = _current_role()
    return role in roles if role else False


def _parse_date(value, field_name: str) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid date for {field_name}") from exc
    raise ValueError(f"Invalid date for {field_name}")


def _generate_job_code() -> str:
    latest_code = db.session.execute(
        select(MaintenanceJob.job_code).order_by(MaintenanceJob.id.desc()).limit(1)
    ).scalar()
    if latest_code:
        match = _CODE_PATTERN.search(latest_code)
        if match:
            number = int(match.group(1)) + 1
        else:
            number = MaintenanceJob.query.count() + 1
    else:
        number = 1
    return f"JOB-{number:03d}"


def _coerce_optional_int(value, field_label: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        value = stripped
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"Invalid {field_label}.") from exc
    return number or None


def _resolve_asset_and_part(payload: dict) -> tuple[Optional[int], Optional[int], Optional[MachineAsset], Optional[MachinePart], Optional[str]]:
    try:
        asset_id = _coerce_optional_int(payload.get("asset_id"), "asset selection")
        part_id = _coerce_optional_int(payload.get("part_id"), "part selection")
    except ValueError as exc:
        return None, None, None, None, str(exc)

    asset = None
    if asset_id is not None:
        asset = MachineAsset.query.get(asset_id)
        if not asset:
            return None, None, None, None, "Selected asset could not be found."

    part = None
    if part_id is not None:
        part = MachinePart.query.get(part_id)
        if not part:
            return None, None, None, None, "Selected part could not be found."

    if part and asset and part.asset_id != asset.id:
        return None, None, None, None, "Selected part does not belong to the chosen asset."

    if asset and part is None:
        parts_count = MachinePart.query.filter_by(asset_id=asset.id).count()
        if parts_count > 0:
            return None, None, None, None, "Select a part for the chosen asset."

    if part and asset is None:
        asset_id = part.asset_id
        asset = part.asset or MachineAsset.query.get(part.asset_id)

    return asset_id, part_id, asset, part, None


def _find_user_by_email(email: Optional[str]) -> Optional[User]:
    if not email:
        return None
    lowered = email.strip().lower()
    if not lowered:
        return None
    return db.session.execute(
        select(User).filter(func.lower(User.email) == lowered)
    ).scalar_one_or_none()


def _send_email(subject: str, recipient: Optional[str], body: str) -> tuple[bool, Optional[str]]:
    if not recipient:
        return False, "No recipient email address was provided."
    try:
        message = Message(subject, recipients=[recipient])
        default_sender = current_app.config.get("MAIL_DEFAULT_SENDER")
        if default_sender:
            message.sender = default_sender
        message.body = body
        mail.send(message)
        return True, f"Notification email sent to {recipient}."
    except Exception as exc:  # pragma: no cover - logging only
        current_app.logger.warning("Failed to send maintenance job email: %s", exc)
        message = "Failed to send the notification email."
        if isinstance(exc, (socket.timeout, TimeoutError)):
            message = "Failed to send the notification email: the mail server timed out."
        elif isinstance(exc, smtplib.SMTPAuthenticationError):
            message = "Failed to send the notification email: authentication failed."
        elif isinstance(exc, smtplib.SMTPConnectError):
            message = "Failed to send the notification email: could not connect to the mail server."
        elif isinstance(exc, smtplib.SMTPRecipientsRefused):
            message = "Failed to send the notification email: the recipient address was rejected."
        elif isinstance(exc, ssl.SSLCertVerificationError):
            message = "Failed to send the notification email: the mail server's SSL certificate could not be verified."
        elif isinstance(exc, ssl.SSLError):
            message = "Failed to send the notification email: a secure connection to the mail server could not be established."
        elif isinstance(exc, OSError) and getattr(exc, "errno", None) in {101, 111}:
            message = "Failed to send the notification email: the mail server could not be reached."
        return False, message


@bp.get("/next-code")
@jwt_required()
def get_next_code():
    if not require_role(
        RoleEnum.production_manager,
        RoleEnum.admin,
        RoleEnum.maintenance_manager,
    ):
        return jsonify({"msg": "Not authorised"}), 403
    return jsonify({"code": _generate_job_code()})


@bp.get("")
@jwt_required()
def list_jobs():
    jobs = (
        MaintenanceJob.query.order_by(MaintenanceJob.created_at.desc())
        .options(joinedload(MaintenanceJob.created_by))
        .options(joinedload(MaintenanceJob.assigned_to))
        .options(joinedload(MaintenanceJob.asset))
        .options(joinedload(MaintenanceJob.part))
        .options(joinedload(MaintenanceJob.materials))
        .options(
            joinedload(MaintenanceJob.outsourced_services).joinedload(
                MaintenanceOutsourcedService.supplier
            )
        )
        .options(
            joinedload(MaintenanceJob.internal_staff_costs).joinedload(
                MaintenanceInternalStaffCost.employee
            )
        )
        .all()
    )
    return jsonify(jobs_schema.dump(jobs))


@bp.get("/<int:job_id>")
@jwt_required()
def get_job(job_id: int):
    job = (
        MaintenanceJob.query.options(joinedload(MaintenanceJob.materials))
        .options(joinedload(MaintenanceJob.created_by))
        .options(joinedload(MaintenanceJob.assigned_to))
        .options(joinedload(MaintenanceJob.asset))
        .options(joinedload(MaintenanceJob.part))
        .options(
            joinedload(MaintenanceJob.outsourced_services).joinedload(
                MaintenanceOutsourcedService.supplier
            )
        )
        .options(
            joinedload(MaintenanceJob.internal_staff_costs).joinedload(
                MaintenanceInternalStaffCost.employee
            )
        )
        .get_or_404(job_id)
    )
    return jsonify(job_schema.dump(job))


@bp.post("")
@jwt_required()
def create_job():
    if not require_role(RoleEnum.production_manager, RoleEnum.admin):
        return jsonify({"msg": "Only Production Managers can create jobs."}), 403

    payload = request.get_json() or {}

    job_category = (payload.get("job_category") or payload.get("title") or "").strip()
    if not job_category:
        return jsonify({"msg": "Job category is required."}), 400

    asset_id, part_id, asset, part, asset_error = _resolve_asset_and_part(payload)
    if asset_error:
        return jsonify({"msg": asset_error}), 400

    priority = (payload.get("priority") or "Normal").strip().title()
    if priority not in _ALLOWED_PRIORITIES:
        priority = "Normal"

    try:
        job_date = _parse_date(payload.get("job_date") or date.today(), "job_date")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    try:
        expected_completion = _parse_date(payload.get("expected_completion"), "expected_completion")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    maint_email = (payload.get("maint_email") or "").strip() or None
    prod_email = (payload.get("prod_email") or "").strip() or None

    user_id = _current_user_id()
    if user_id is None:
        return jsonify({"msg": "Invalid token subject"}), 422

    if not prod_email:
        user = User.query.get(user_id)
        prod_email = user.email if user else None
    else:
        user = User.query.get(user_id)

    requested_code = (payload.get("job_code") or "").strip() or None
    job_code = requested_code or _generate_job_code()
    if MaintenanceJob.query.filter_by(job_code=job_code).first():
        job_code = _generate_job_code()

    job = MaintenanceJob(
        job_code=job_code,
        job_date=job_date or date.today(),
        title=job_category,
        job_category=job_category,
        priority=priority,
        location=(payload.get("location") or None),
        description=(payload.get("description") or None),
        expected_completion=expected_completion,
        maint_email=maint_email,
        prod_email=prod_email,
        created_by_id=user_id,
        status=MaintenanceJobStatus.IN_PROGRESS,
        prod_submitted_at=datetime.utcnow(),
        asset_id=asset_id,
        part_id=part_id,
    )

    if asset_id is not None and asset is not None:
        job.asset = asset
    if part_id is not None and part is not None:
        job.part = part

    maint_user = _find_user_by_email(maint_email)
    if maint_user:
        job.assigned_to = maint_user

    db.session.add(job)
    db.session.commit()

    link = url_for("ui.machines_page", _external=True) + "#maintenance-section"
    body_lines = [
        "Hello,",
        "",
        f"A new maintenance job has been assigned: {job.job_code}.",
        f"Job category: {job.job_category}",
        f"Priority: {job.priority}",
        f"Location: {job.location or 'N/A'}",
    ]
    if job.asset:
        asset_label_parts = [value for value in [job.asset.code, job.asset.name] if value]
        asset_label = " — ".join(asset_label_parts) if asset_label_parts else "N/A"
        body_lines.append(f"Asset: {asset_label}")
    if job.part:
        part_label_parts = [value for value in [job.part.part_number, job.part.name] if value]
        part_label = " — ".join(part_label_parts) if part_label_parts else "N/A"
        body_lines.append(f"Part: {part_label}")
    if job.expected_completion:
        body_lines.append(
            f"Expected completion: {job.expected_completion.strftime('%Y-%m-%d')}"
        )
    if job.description:
        body_lines.extend(["", "Job description:", job.description])
    body_lines.extend(["", f"View job: {link}"])
    body = "\n".join(body_lines)
    email_sent, email_message = _send_email(
        f"New Maintenance Job Assigned: {job.job_code}", maint_email, body
    )

    response_payload = {
        "job": job_schema.dump(job),
        "email_notification": {
            "sent": email_sent,
            "recipient": maint_email,
            "message": email_message,
        },
    }

    return jsonify(response_payload), 201


@bp.patch("/<int:job_id>")
@jwt_required()
def update_job(job_id: int):
    job = (
        MaintenanceJob.query.options(joinedload(MaintenanceJob.materials))
        .options(joinedload(MaintenanceJob.asset))
        .options(joinedload(MaintenanceJob.part))
        .get_or_404(job_id)
    )

    role = _current_role()
    if role not in {RoleEnum.maintenance_manager, RoleEnum.admin, RoleEnum.production_manager}:
        return jsonify({"msg": "Not authorised."}), 403

    payload = request.get_json() or {}

    if role in {RoleEnum.production_manager, RoleEnum.admin} and job.status == MaintenanceJobStatus.NEW:
        # allow minor updates before submission
        updatable_fields = {"priority", "location", "description", "expected_completion", "maint_email", "job_date"}
        for field in updatable_fields:
            if field not in payload:
                continue
            value = payload[field]
            if field == "priority":
                if value:
                    normalized = str(value).strip().title()
                    job.priority = normalized if normalized in _ALLOWED_PRIORITIES else job.priority
            elif field == "expected_completion":
                try:
                    job.expected_completion = _parse_date(value, "expected_completion")
                except ValueError as exc:
                    return jsonify({"msg": str(exc)}), 400
            elif field == "job_date":
                try:
                    job.job_date = _parse_date(value, "job_date") or job.job_date
                except ValueError as exc:
                    return jsonify({"msg": str(exc)}), 400
            else:
                setattr(job, field, value or None)

        if "job_category" in payload or ("title" in payload and "job_category" not in payload):
            raw_category = payload.get("job_category")
            if "job_category" not in payload:
                raw_category = payload.get("title")
            if isinstance(raw_category, str):
                category_value = raw_category.strip()
            elif raw_category is None:
                category_value = ""
            else:
                category_value = str(raw_category).strip()
            if not category_value:
                return jsonify({"msg": "Job category is required."}), 400
            job.job_category = category_value
            job.title = category_value

        if "asset_id" in payload or "part_id" in payload:
            asset_id, part_id, asset, part, asset_error = _resolve_asset_and_part(payload)
            if asset_error:
                return jsonify({"msg": asset_error}), 400
            job.asset_id = asset_id
            job.part_id = part_id
            job.asset = asset if asset_id is not None else None
            job.part = part if part_id is not None else None

        db.session.commit()
        return jsonify(job_schema.dump(job))

    if role not in {RoleEnum.maintenance_manager, RoleEnum.admin}:
        return jsonify({"msg": "Only Maintenance Managers can update maintenance details."}), 403

    try:
        job.job_started_date = _parse_date(payload.get("job_started_date"), "job_started_date")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    try:
        job.job_finished_date = _parse_date(payload.get("job_finished_date"), "job_finished_date")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    job.maintenance_notes = (payload.get("maintenance_notes") or None)

    materials_payload = payload.get("materials") or []
    job.materials[:] = []
    total_cost = Decimal("0")
    for item in materials_payload:
        if not isinstance(item, dict):
            continue
        name = (item.get("material_name") or "").strip()
        if not name:
            continue
        units = (item.get("units") or "").strip() or None
        cost_value = item.get("cost")
        cost_decimal = None
        if cost_value not in (None, ""):
            try:
                cost_decimal = Decimal(str(cost_value))
            except (ValueError, ArithmeticError):
                return jsonify({"msg": f"Invalid cost for material '{name}'."}), 400
            if cost_decimal < 0:
                return jsonify({"msg": f"Cost for material '{name}' must be non-negative."}), 400
        material = MaintenanceMaterial(material_name=name, units=units, cost=cost_decimal)
        job.materials.append(material)
        if cost_decimal is not None:
            total_cost += cost_decimal

    start_date = job.job_started_date
    end_date = job.job_finished_date
    internal_payload = payload.get("internal_staff_costs") or []
    job.internal_staff_costs[:] = []
    for item in internal_payload:
        if not isinstance(item, dict):
            continue
        employee_id_raw = item.get("employee_id")
        description = (item.get("work_description") or "").strip()
        date_value = item.get("service_date")
        hours_value = item.get("engaged_hours")
        rate_value = item.get("hourly_rate")
        cost_value = item.get("cost")

        if (
            not employee_id_raw
            and not description
            and not date_value
            and not hours_value
            and not rate_value
            and not cost_value
        ):
            continue

        try:
            employee_id = int(employee_id_raw)
        except (TypeError, ValueError):
            return (
                jsonify({"msg": "Select a valid employee for internal staff cost."}),
                400,
            )
        employee = TeamMember.query.get(employee_id)
        if not employee:
            return jsonify({"msg": "Selected employee does not exist."}), 400

        if not description:
            return jsonify({"msg": "Work description is required for internal staff cost."}), 400

        try:
            service_date = _parse_date(date_value, "service_date")
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400
        if service_date is None:
            return jsonify({"msg": "Service date is required for internal staff cost."}), 400
        if start_date and service_date < start_date:
            return (
                jsonify({"msg": "Service date cannot be before the job started date."}),
                400,
            )
        if end_date and service_date > end_date:
            return (
                jsonify({"msg": "Service date cannot be after the job finished date."}),
                400,
            )

        try:
            cost_decimal = Decimal(str(cost_value))
        except (TypeError, ValueError, ArithmeticError):
            return jsonify({"msg": "Cost is required for internal staff cost."}), 400
        if cost_decimal < 0:
            return jsonify({"msg": "Cost must be non-negative for internal staff cost."}), 400

        engaged_hours = None
        if hours_value not in (None, ""):
            try:
                engaged_hours = Decimal(str(hours_value))
            except (TypeError, ValueError, ArithmeticError):
                return (
                    jsonify({"msg": "Invalid engaged hours for internal staff cost."}),
                    400,
                )
            if engaged_hours < 0:
                return (
                    jsonify({"msg": "Engaged hours must be non-negative for internal staff cost."}),
                    400,
                )

        hourly_rate = None
        if rate_value not in (None, ""):
            try:
                hourly_rate = Decimal(str(rate_value))
            except (TypeError, ValueError, ArithmeticError):
                return (
                    jsonify({"msg": "Invalid hourly rate for internal staff cost."}),
                    400,
                )
            if hourly_rate < 0:
                return (
                    jsonify({"msg": "Hourly rate must be non-negative for internal staff cost."}),
                    400,
                )

        staff_cost = MaintenanceInternalStaffCost(
            employee=employee,
            service_date=service_date,
            work_description=description,
            engaged_hours=engaged_hours,
            hourly_rate=hourly_rate,
            cost=cost_decimal,
        )
        job.internal_staff_costs.append(staff_cost)
        total_cost += cost_decimal

    outsourced_payload = payload.get("outsourced_services") or []
    job.outsourced_services[:] = []
    for item in outsourced_payload:
        if not isinstance(item, dict):
            continue
        supplier_id_raw = item.get("supplier_id")
        description = (item.get("service_description") or "").strip()
        if not supplier_id_raw and not description and not item.get("service_date") and not item.get("cost"):
            continue
        try:
            supplier_id = int(supplier_id_raw)
        except (TypeError, ValueError):
            return jsonify({"msg": "Select a valid outsourced party."}), 400
        supplier = ServiceSupplier.query.get(supplier_id)
        if not supplier:
            return jsonify({"msg": "Selected outsourced party does not exist."}), 400
        if not description:
            return jsonify({"msg": "Service description is required for outsourced services."}), 400
        try:
            service_date = _parse_date(item.get("service_date"), "service_date")
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400
        if service_date is None:
            return jsonify({"msg": "Service date is required for outsourced services."}), 400
        if start_date and service_date < start_date:
            return jsonify({"msg": "Service date cannot be before the job started date."}), 400
        if end_date and service_date > end_date:
            return jsonify({"msg": "Service date cannot be after the job finished date."}), 400
        cost_value = item.get("cost")
        try:
            cost_decimal = Decimal(str(cost_value))
        except (TypeError, ValueError, ArithmeticError):
            return jsonify({"msg": "Cost is required for outsourced services."}), 400
        if cost_decimal < 0:
            return jsonify({"msg": "Cost must be non-negative for outsourced services."}), 400
        hours_value = item.get("engaged_hours")
        engaged_hours = None
        if hours_value not in (None, ""):
            try:
                engaged_hours = Decimal(str(hours_value))
            except (TypeError, ValueError, ArithmeticError):
                return jsonify({"msg": "Invalid engaged hours for outsourced services."}), 400
            if engaged_hours < 0:
                return jsonify({"msg": "Engaged hours must be non-negative for outsourced services."}), 400
        outsourced_service = MaintenanceOutsourcedService(
            supplier=supplier,
            service_date=service_date,
            service_description=description,
            engaged_hours=engaged_hours,
            cost=cost_decimal,
        )
        job.outsourced_services.append(outsourced_service)
        total_cost += cost_decimal

    job.total_cost = total_cost
    job.maint_submitted_at = datetime.utcnow()
    job.status = MaintenanceJobStatus.COMPLETED
    if "prod_email" in payload:
        job.prod_email = (payload.get("prod_email") or None)

    maint_user = _find_user_by_email(job.maint_email)
    if maint_user and job.assigned_to_id != maint_user.id:
        job.assigned_to = maint_user

    actor_id = _current_user_id()
    if actor_id and job.assigned_to_id != actor_id and role in {RoleEnum.maintenance_manager, RoleEnum.admin}:
        job.assigned_to_id = actor_id

    db.session.commit()

    duration_text = ""
    if job.job_started_date and job.job_finished_date:
        duration_days = (job.job_finished_date - job.job_started_date).days
        if duration_days >= 0:
            duration_text = f"Job duration: {duration_days} day(s)."

    total_cost_value = job.total_cost or Decimal("0")
    if not isinstance(total_cost_value, Decimal):
        try:
            total_cost_value = Decimal(str(total_cost_value))
        except Exception:
            total_cost_value = Decimal("0")

    body_lines = [
        "Hello,",
        "",
        f"Maintenance job {job.job_code} has been completed.",
        f"Job category: {job.job_category}",
        f"Total cost: {total_cost_value:.2f}",
    ]
    if job.asset:
        asset_label_parts = [value for value in [job.asset.code, job.asset.name] if value]
        asset_label = " — ".join(asset_label_parts) if asset_label_parts else "N/A"
        body_lines.append(f"Asset: {asset_label}")
    if job.part:
        part_label_parts = [value for value in [job.part.part_number, job.part.name] if value]
        part_label = " — ".join(part_label_parts) if part_label_parts else "N/A"
        body_lines.append(f"Part: {part_label}")
    if duration_text:
        body_lines.append(duration_text)
    if job.maintenance_notes:
        body_lines.extend(["", "Remarks:", job.maintenance_notes])
    body_lines.extend(["", "Thank you."])
    body = "\n".join(body_lines)
    email_sent, email_message = _send_email(
        f"Maintenance Job Completed: {job.job_code}", job.prod_email, body
    )

    response_payload = {
        "job": job_schema.dump(job),
        "email_notification": {
            "sent": email_sent,
            "recipient": job.prod_email,
            "message": email_message,
        },
    }

    return jsonify(response_payload)


@bp.post("/<int:job_id>/reopen")
@jwt_required()
def reopen_job(job_id: int):
    if not require_role(RoleEnum.production_manager, RoleEnum.admin):
        return jsonify({"msg": "Only Production Managers can reopen jobs."}), 403

    job = MaintenanceJob.query.get_or_404(job_id)
    job.status = MaintenanceJobStatus.NEW
    job.prod_submitted_at = None
    job.maint_submitted_at = None
    job.assigned_to_id = None
    db.session.commit()
    return jsonify(job_schema.dump(job))
