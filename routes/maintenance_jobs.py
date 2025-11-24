from __future__ import annotations

import html
import os
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from requests import exceptions as requests_exceptions
from flask import Blueprint, jsonify, request, url_for, current_app
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import joinedload

from extensions import db
from maintenance_status import (
    get_status_badge_class,
    get_status_code,
    get_status_color,
    get_status_label,
)
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

RESEND_ENDPOINT = "https://api.resend.com/emails"
RESEND_DEFAULT_SENDER = "Samprox ERP <no-reply@samprox.lk>"

_CODE_PATTERN = re.compile(r"(\d+)$")
_ALLOWED_PRIORITIES = {"Normal", "Urgent", "Critical"}
_COLOMBO_TZ = ZoneInfo("Asia/Colombo")
_PENDING_STATUS_VALUES = {
    MaintenanceJobStatus.SUBMITTED.value,
    MaintenanceJobStatus.FORWARDED_TO_MAINTENANCE.value,
    MaintenanceJobStatus.NOT_YET_STARTED.value,
    MaintenanceJobStatus.IN_PROGRESS.value,
    MaintenanceJobStatus.AWAITING_PARTS.value,
    MaintenanceJobStatus.ON_HOLD.value,
    MaintenanceJobStatus.TESTING.value,
    MaintenanceJobStatus.COMPLETED_MAINTENANCE.value,
    MaintenanceJobStatus.RETURNED_TO_PRODUCTION.value,
    MaintenanceJobStatus.REOPENED.value,
}
_COMPLETED_STATUS_VALUES = {MaintenanceJobStatus.COMPLETED_VERIFIED.value}
_MAINTENANCE_EDITABLE_STATUSES = {
    MaintenanceJobStatus.NOT_YET_STARTED,
    MaintenanceJobStatus.IN_PROGRESS,
    MaintenanceJobStatus.AWAITING_PARTS,
    MaintenanceJobStatus.ON_HOLD,
    MaintenanceJobStatus.TESTING,
    MaintenanceJobStatus.COMPLETED_MAINTENANCE,
}


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


def _as_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # pragma: no cover - defensive
        return Decimal("0")


def _status_value(status) -> str:
    if isinstance(status, MaintenanceJobStatus):
        return status.value
    if status is None:
        return ""
    return str(status)


def _maybe_forward_to_maintenance(job: MaintenanceJob, *, role: Optional[RoleEnum] = None) -> None:
    current_status = get_status_code(job.status)
    if role in {RoleEnum.admin, RoleEnum.maintenance_manager} and current_status == MaintenanceJobStatus.SUBMITTED.value:
        job.status = MaintenanceJobStatus.FORWARDED_TO_MAINTENANCE.value
        db.session.commit()


def _quantize(value: Decimal, pattern: str) -> Decimal:
    return value.quantize(Decimal(pattern), rounding=ROUND_HALF_UP)


def _currency_number(value: Decimal) -> float:
    return float(_quantize(value, "0.01"))


def _hours_number(value: Decimal) -> float:
    return float(_quantize(value, "0.1"))


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


def _coerce_part_id_list(value: Optional[object]) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]
    part_ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_values:
        try:
            number = _coerce_optional_int(raw, "part selection")
        except ValueError:
            raise
        if number is None or number in seen:
            continue
        seen.add(number)
        part_ids.append(number)
    return part_ids


def _resolve_asset_and_parts(
    payload: dict,
) -> tuple[
    Optional[int],
    list[int],
    Optional[MachineAsset],
    list[MachinePart],
    Optional[str],
]:
    try:
        asset_id = _coerce_optional_int(payload.get("asset_id"), "asset selection")
        part_ids = _coerce_part_id_list(payload.get("part_ids", payload.get("part_id")))
    except ValueError as exc:
        return None, [], None, [], str(exc)

    asset: Optional[MachineAsset] = None
    if asset_id is not None:
        asset = MachineAsset.query.get(asset_id)
        if not asset:
            return None, [], None, [], "Selected asset could not be found."

    parts: list[MachinePart] = []
    if part_ids:
        parts = MachinePart.query.filter(MachinePart.id.in_(part_ids)).all()
        if len(parts) != len(part_ids):
            return None, [], None, [], "One or more selected parts could not be found."

    part_asset_ids = {part.asset_id for part in parts if part.asset_id is not None}
    if len(part_asset_ids) > 1:
        return None, [], None, [], "Selected parts must belong to the same asset."

    if asset and part_asset_ids and (asset.id not in part_asset_ids):
        return None, [], None, [], "Selected part does not belong to the chosen asset."

    if parts and asset is None and part_asset_ids:
        asset_id = parts[0].asset_id
        asset = parts[0].asset or MachineAsset.query.get(asset_id)

    if asset and not parts:
        parts_count = MachinePart.query.filter_by(asset_id=asset.id).count()
        if parts_count > 0:
            return None, [], None, [], "Select at least one part for the chosen asset."

    return asset_id, part_ids, asset, parts, None


def _find_user_by_email(email: Optional[str]) -> Optional[User]:
    if not email:
        return None
    lowered = email.strip().lower()
    if not lowered:
        return None
    return db.session.execute(
        select(User).filter(func.lower(User.email) == lowered)
    ).scalar_one_or_none()


def _normalize_recipients(addresses: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for address in addresses:
        if not address:
            continue
        cleaned = address.strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(cleaned)
    return normalized


def _split_recipients(value: Optional[str]) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[;,]", value)
    return _normalize_recipients(parts)


def _resolve_sender() -> str:
    sender = current_app.config.get("RESEND_DEFAULT_SENDER")
    if isinstance(sender, (list, tuple)) and sender:
        sender = sender[0]
    if isinstance(sender, str) and sender.strip():
        return sender.strip()
    sender = current_app.config.get("MAIL_DEFAULT_SENDER")
    if isinstance(sender, (list, tuple)) and sender:
        sender = sender[0]
    if isinstance(sender, str) and sender.strip():
        return sender.strip()
    return RESEND_DEFAULT_SENDER


def _send_email(subject: str, recipient: Optional[str], body: str) -> tuple[bool, Optional[str]]:
    recipients = _split_recipients(recipient)
    if not recipients:
        return False, "No recipient email address was provided."

    primary_recipient, *cc_recipients = recipients
    primary_recipient = primary_recipient.strip()
    recipient_set = {primary_recipient.lower()}

    filtered_cc: list[str] = []
    for address in cc_recipients:
        lowered = address.lower()
        if lowered in recipient_set:
            continue
        filtered_cc.append(address)
        recipient_set.add(lowered)

    bcc_config = current_app.config.get("MAIL_DEFAULT_BCC", [])
    if isinstance(bcc_config, str):
        bcc_config = [bcc_config]
    filtered_bcc = [
        address.strip()
        for address in bcc_config or []
        if address and address.strip().lower() not in recipient_set
    ]

    html_body = html.escape(body).replace("\n", "<br>")

    data: dict[str, object] = {
        "from": _resolve_sender(),
        "to": [primary_recipient],
        "subject": subject,
        "text": body,
        "html": html_body,
    }
    if filtered_cc:
        data["cc"] = filtered_cc
    if filtered_bcc:
        data["bcc"] = filtered_bcc

    try:
        _send_email_via_resend(data)
    except KeyError as exc:  # pragma: no cover - configuration error
        current_app.logger.warning(
            "RESEND_API_KEY is not configured for maintenance job emails."
        )
        message = "Failed to send the notification email: email service is not configured."
    except requests_exceptions.Timeout as exc:
        current_app.logger.warning(
            "Failed to send maintenance job email due to timeout: %s", exc, exc_info=exc
        )
        message = "Failed to send the notification email: the email service timed out."
    except requests_exceptions.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in {401, 403}:
            message = "Failed to send the notification email: authentication failed."
        else:
            message = "Failed to send the notification email: the email service returned an error."
        current_app.logger.warning(
            "Failed to send maintenance job email (status %s): %s",
            status_code,
            exc,
            exc_info=exc,
        )
    except requests_exceptions.SSLError as exc:
        current_app.logger.warning(
            "Failed to send maintenance job email due to SSL error: %s",
            exc,
            exc_info=exc,
        )
        message = "Failed to send the notification email: a secure connection to the email service could not be established."
    except requests_exceptions.ConnectionError as exc:
        current_app.logger.warning(
            "Failed to send maintenance job email due to connection error: %s",
            exc,
            exc_info=exc,
        )
        message = "Failed to send the notification email: the email service could not be reached."
    except requests_exceptions.RequestException as exc:  # pragma: no cover - logging only
        current_app.logger.warning(
            "Failed to send maintenance job email: %s", exc, exc_info=exc
        )
        message = "Failed to send the notification email."
    else:
        return True, f"Notification email sent to {primary_recipient}."

    return False, f"{message} Please notify them manually."


def _resend_api_key() -> str:
    api_key = current_app.config.get("RESEND_API_KEY")
    if isinstance(api_key, str):
        api_key = api_key.strip()
    if not api_key:
        api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise KeyError("RESEND_API_KEY")
    return api_key


def _send_email_via_resend(data: dict) -> None:
    api_key = _resend_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        RESEND_ENDPOINT,
        headers=headers,
        json=data,
        timeout=15,
    )
    response.raise_for_status()


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
    query = (
        MaintenanceJob.query.order_by(MaintenanceJob.created_at.desc())
        .options(joinedload(MaintenanceJob.created_by))
        .options(joinedload(MaintenanceJob.assigned_to))
        .options(joinedload(MaintenanceJob.asset))
        .options(joinedload(MaintenanceJob.part))
        .options(joinedload(MaintenanceJob.parts))
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
    )

    try:
        start_date = _parse_date(request.args.get("start_date"), "start_date")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    try:
        end_date = _parse_date(request.args.get("end_date"), "end_date")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    if start_date and end_date and start_date > end_date:
        start_date, end_date = end_date, start_date

    if start_date:
        query = query.filter(MaintenanceJob.job_date >= start_date)
    if end_date:
        query = query.filter(MaintenanceJob.job_date <= end_date)

    limit = request.args.get("limit", type=int)
    offset = request.args.get("offset", type=int)

    if offset is not None and offset > 0:
        query = query.offset(offset)
    if limit is not None and limit > 0:
        query = query.limit(limit)

    jobs = query.all()
    role = _current_role()
    for job in jobs:
        _maybe_forward_to_maintenance(job, role=role)
    return jsonify(jobs_schema.dump(jobs))


@bp.get("/summary")
@jwt_required()
def jobs_summary():
    try:
        requested_start = _parse_date(request.args.get("start_date"), "start_date")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    try:
        requested_end = _parse_date(request.args.get("end_date"), "end_date")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    min_max_row = db.session.execute(
        select(
            func.min(MaintenanceJob.job_date),
            func.max(MaintenanceJob.job_date),
        )
    ).first()

    min_job_date, max_job_date = min_max_row if min_max_row else (None, None)

    today_local = datetime.now(_COLOMBO_TZ).date()

    start_date = requested_start or min_job_date or max_job_date or today_local
    end_date = requested_end or max_job_date or min_job_date or start_date

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    jobs_query = select(
        MaintenanceJob.id.label("id"),
        MaintenanceJob.status.label("status"),
        MaintenanceJob.expected_completion.label("expected_completion"),
    ).where(
        MaintenanceJob.job_date >= start_date,
        MaintenanceJob.job_date <= end_date,
    )

    jobs_rows = db.session.execute(jobs_query).all()
    jobs_initiated = len(jobs_rows)
    pending_count = 0
    completed_count = 0
    overdue_count = 0

    for row in jobs_rows:
        status_label = _status_value(row.status).upper()
        if status_label in _COMPLETED_STATUS_VALUES:
            completed_count += 1
        if status_label in _PENDING_STATUS_VALUES:
            pending_count += 1
        expected_completion = row.expected_completion
        if (
            expected_completion
            and expected_completion < today_local
            and status_label not in _COMPLETED_STATUS_VALUES
        ):
            overdue_count += 1

    materials_total = _as_decimal(
        db.session.execute(
            select(func.coalesce(func.sum(MaintenanceMaterial.cost), 0)).join(
                MaintenanceJob,
                MaintenanceMaterial.maintenance_job_id == MaintenanceJob.id,
            )
            .where(MaintenanceJob.job_date >= start_date)
            .where(MaintenanceJob.job_date <= end_date)
        ).scalar()
    )

    outsourced_rows = db.session.execute(
        select(
            MaintenanceOutsourcedService.cost.label("cost"),
            MaintenanceOutsourcedService.service_date.label("service_date"),
            MaintenanceJob.job_date.label("job_date"),
        )
        .join(
            MaintenanceJob,
            MaintenanceOutsourcedService.maintenance_job_id == MaintenanceJob.id,
        )
        .where(
            or_(
                and_(
                    MaintenanceOutsourcedService.service_date.isnot(None),
                    MaintenanceOutsourcedService.service_date >= start_date,
                    MaintenanceOutsourcedService.service_date <= end_date,
                ),
                and_(
                    MaintenanceOutsourcedService.service_date.is_(None),
                    MaintenanceJob.job_date >= start_date,
                    MaintenanceJob.job_date <= end_date,
                ),
            )
        )
    ).all()

    outsourced_total = Decimal("0")
    for row in outsourced_rows:
        service_date = row.service_date or row.job_date
        if not service_date:
            continue
        if service_date < start_date or service_date > end_date:
            continue
        outsourced_total += _as_decimal(row.cost)

    internal_rows = db.session.execute(
        select(
            MaintenanceInternalStaffCost.cost.label("cost"),
            MaintenanceInternalStaffCost.hourly_rate.label("hourly_rate"),
            MaintenanceInternalStaffCost.engaged_hours.label("engaged_hours"),
            MaintenanceInternalStaffCost.service_date.label("service_date"),
            MaintenanceJob.job_date.label("job_date"),
            TeamMember.reg_number.label("employee_code"),
        )
        .join(
            MaintenanceJob,
            MaintenanceInternalStaffCost.maintenance_job_id == MaintenanceJob.id,
        )
        .join(
            TeamMember,
            MaintenanceInternalStaffCost.employee_id == TeamMember.id,
            isouter=True,
        )
        .where(
            or_(
                and_(
                    MaintenanceInternalStaffCost.service_date.isnot(None),
                    MaintenanceInternalStaffCost.service_date >= start_date,
                    MaintenanceInternalStaffCost.service_date <= end_date,
                ),
                and_(
                    MaintenanceInternalStaffCost.service_date.is_(None),
                    MaintenanceJob.job_date >= start_date,
                    MaintenanceJob.job_date <= end_date,
                ),
            )
        )
    ).all()

    internal_total = Decimal("0")
    internal_hours_total = Decimal("0")
    internal_hours_e023 = Decimal("0")
    internal_hours_other = Decimal("0")

    for row in internal_rows:
        service_date = row.service_date or row.job_date
        if not service_date:
            continue
        if service_date < start_date or service_date > end_date:
            continue
        hours = _as_decimal(row.engaged_hours)
        internal_hours_total += hours
        employee_code = (row.employee_code or "").strip().upper()
        if employee_code == "E023":
            internal_hours_e023 += hours
        else:
            internal_hours_other += hours
        raw_cost = row.cost
        cost = _as_decimal(raw_cost)
        if raw_cost is None:
            cost = _as_decimal(row.hourly_rate) * hours
        internal_total += cost

    grand_total = materials_total + outsourced_total + internal_total

    available_start = min_job_date or start_date
    available_end = max_job_date or end_date

    payload = {
        "period": {
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
        "available_range": {
            "start_date": available_start.isoformat() if available_start else None,
            "end_date": available_end.isoformat() if available_end else None,
        },
        "totals": {
            "material": _currency_number(materials_total),
            "outsourced": _currency_number(outsourced_total),
            "internal": _currency_number(internal_total),
            "grand": _currency_number(grand_total),
        },
        "jobs": {
            "initiated": jobs_initiated,
            "pending": pending_count,
            "completed": completed_count,
            "overdue": overdue_count,
        },
        "hours": {
            "total": _hours_number(internal_hours_total),
            "e023": _hours_number(internal_hours_e023),
            "other": _hours_number(internal_hours_other),
        },
    }

    return jsonify(payload)


@bp.get("/<int:job_id>")
@jwt_required()
def get_job(job_id: int):
    job = (
        MaintenanceJob.query.options(joinedload(MaintenanceJob.materials))
        .options(joinedload(MaintenanceJob.created_by))
        .options(joinedload(MaintenanceJob.assigned_to))
        .options(joinedload(MaintenanceJob.asset))
        .options(joinedload(MaintenanceJob.part))
        .options(joinedload(MaintenanceJob.parts))
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
    _maybe_forward_to_maintenance(job, role=_current_role())
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

    asset_id, part_ids, asset, parts, asset_error = _resolve_asset_and_parts(payload)
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
        status=MaintenanceJobStatus.SUBMITTED.value,
        prod_submitted_at=datetime.utcnow(),
        asset_id=asset_id,
        part_id=part_ids[0] if part_ids else None,
    )

    if asset_id is not None and asset is not None:
        job.asset = asset
    if parts:
        job.parts = parts
        job.part = parts[0]

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
    if job.parts:
        part_labels = []
        for part in job.parts:
            part_label_parts = [value for value in [part.part_number, part.name] if value]
            part_labels.append(" — ".join(part_label_parts) if part_label_parts else "N/A")
        body_lines.append(f"Part(s): {', '.join(part_labels)}")
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
        .options(joinedload(MaintenanceJob.created_by))
        .options(joinedload(MaintenanceJob.asset))
        .options(joinedload(MaintenanceJob.part))
        .options(joinedload(MaintenanceJob.parts))
        .get_or_404(job_id)
    )

    role = _current_role()
    if role not in {RoleEnum.maintenance_manager, RoleEnum.admin, RoleEnum.production_manager}:
        return jsonify({"msg": "Not authorised."}), 403

    payload = request.get_json() or {}

    job_status_code = get_status_code(job.status)
    admin_can_edit_production = role == RoleEnum.admin
    production_can_edit = (
        role == RoleEnum.production_manager
        and job_status_code == MaintenanceJobStatus.SUBMITTED.value
    )

    if role in {RoleEnum.production_manager, RoleEnum.admin} and (
        admin_can_edit_production or production_can_edit
    ):
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

        if "asset_id" in payload or "part_id" in payload or "part_ids" in payload:
            asset_id, part_ids, asset, parts, asset_error = _resolve_asset_and_parts(payload)
            if asset_error:
                return jsonify({"msg": asset_error}), 400
            job.asset_id = asset_id
            job.part_id = part_ids[0] if part_ids else None
            job.asset = asset if asset_id is not None else None
            job.parts = parts
            job.part = parts[0] if parts else None

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

    requested_status_raw = payload.get("status")
    requested_status = None
    if requested_status_raw not in (None, ""):
        try:
            requested_status = MaintenanceJobStatus(str(requested_status_raw))
        except ValueError:
            return jsonify({"msg": "Invalid maintenance status."}), 400
        if requested_status not in _MAINTENANCE_EDITABLE_STATUSES:
            return jsonify({"msg": "Status cannot be set by maintenance."}), 400

    send_to_production = bool(payload.get("send_to_production"))

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
    if requested_status:
        job.status = requested_status.value
    if send_to_production:
        job.status = MaintenanceJobStatus.RETURNED_TO_PRODUCTION.value
        job.maint_submitted_at = datetime.utcnow()
    if "prod_email" in payload:
        job.prod_email = (payload.get("prod_email") or None)

    maint_user = _find_user_by_email(job.maint_email)
    if maint_user and job.assigned_to_id != maint_user.id:
        job.assigned_to = maint_user

    actor_id = _current_user_id()
    if actor_id and job.assigned_to_id != actor_id and role in {RoleEnum.maintenance_manager, RoleEnum.admin}:
        job.assigned_to_id = actor_id

    db.session.commit()

    if not send_to_production:
        return jsonify(job_schema.dump(job))

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

    status_label = get_status_label(job.status) or "Maintenance job update"
    body_lines = [
        "Hello,",
        "",
        f"Maintenance job {job.job_code} status: {status_label}.",
        f"Job category: {job.job_category}",
        f"Total cost: {total_cost_value:.2f}",
    ]
    if job.asset:
        asset_label_parts = [value for value in [job.asset.code, job.asset.name] if value]
        asset_label = " — ".join(asset_label_parts) if asset_label_parts else "N/A"
        body_lines.append(f"Asset: {asset_label}")
    if job.parts:
        part_labels = []
        for part in job.parts:
            part_label_parts = [value for value in [part.part_number, part.name] if value]
            part_labels.append(" — ".join(part_label_parts) if part_label_parts else "N/A")
        body_lines.append(f"Part(s): {', '.join(part_labels)}")
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
    job.status = MaintenanceJobStatus.REOPENED.value
    job.prod_submitted_at = job.prod_submitted_at or datetime.utcnow()
    db.session.commit()
    return jsonify(job_schema.dump(job))


@bp.post("/<int:job_id>/verify")
@jwt_required()
def verify_job(job_id: int):
    if not require_role(RoleEnum.production_manager, RoleEnum.admin):
        return jsonify({"msg": "Only Production Managers can confirm completion."}), 403

    job = MaintenanceJob.query.get_or_404(job_id)
    job.status = MaintenanceJobStatus.COMPLETED_VERIFIED.value
    job.prod_submitted_at = job.prod_submitted_at or datetime.utcnow()
    db.session.commit()
    return jsonify(job_schema.dump(job))
