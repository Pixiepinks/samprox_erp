from __future__ import annotations

import re
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
    MaintenanceJob,
    MaintenanceJobStatus,
    MaintenanceMaterial,
    RoleEnum,
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


def _find_user_by_email(email: Optional[str]) -> Optional[User]:
    if not email:
        return None
    lowered = email.strip().lower()
    if not lowered:
        return None
    return db.session.execute(
        select(User).filter(func.lower(User.email) == lowered)
    ).scalar_one_or_none()


def _send_email(subject: str, recipient: Optional[str], body: str) -> None:
    if not recipient:
        return
    try:
        message = Message(subject, recipients=[recipient])
        default_sender = current_app.config.get("MAIL_DEFAULT_SENDER")
        if default_sender:
            message.sender = default_sender
        message.body = body
        mail.send(message)
    except Exception as exc:  # pragma: no cover - logging only
        current_app.logger.warning("Failed to send maintenance job email: %s", exc)


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
        .get_or_404(job_id)
    )
    return jsonify(job_schema.dump(job))


@bp.post("")
@jwt_required()
def create_job():
    if not require_role(RoleEnum.production_manager, RoleEnum.admin):
        return jsonify({"msg": "Only Production Managers can create jobs."}), 403

    payload = request.get_json() or {}

    title = (payload.get("title") or "").strip()
    if not title:
        return jsonify({"msg": "Title is required."}), 400

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
        title=title,
        priority=priority,
        location=(payload.get("location") or None),
        description=(payload.get("description") or None),
        expected_completion=expected_completion,
        maint_email=maint_email,
        prod_email=prod_email,
        created_by_id=user_id,
        status=MaintenanceJobStatus.IN_PROGRESS,
        prod_submitted_at=datetime.utcnow(),
    )

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
        f"Title: {job.title}",
        f"Priority: {job.priority}",
        f"Location: {job.location or 'N/A'}",
    ]
    if job.expected_completion:
        body_lines.append(
            f"Expected completion: {job.expected_completion.strftime('%Y-%m-%d')}"
        )
    if job.description:
        body_lines.extend(["", "Job description:", job.description])
    body_lines.extend(["", f"View job: {link}"])
    body = "\n".join(body_lines)
    _send_email(f"New Maintenance Job Assigned: {job.job_code}", maint_email, body)

    return jsonify(job_schema.dump(job)), 201


@bp.patch("/<int:job_id>")
@jwt_required()
def update_job(job_id: int):
    job = MaintenanceJob.query.options(joinedload(MaintenanceJob.materials)).get_or_404(job_id)

    role = _current_role()
    if role not in {RoleEnum.maintenance_manager, RoleEnum.admin, RoleEnum.production_manager}:
        return jsonify({"msg": "Not authorised."}), 403

    payload = request.get_json() or {}

    if role in {RoleEnum.production_manager, RoleEnum.admin} and job.status == MaintenanceJobStatus.NEW:
        # allow minor updates before submission
        updatable_fields = {"title", "priority", "location", "description", "expected_completion", "maint_email", "job_date"}
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
        material = MaintenanceMaterial(material_name=name, units=units, cost=cost_decimal)
        job.materials.append(material)
        if cost_decimal is not None:
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
        f"Title: {job.title}",
        f"Total cost: {total_cost_value:.2f}",
    ]
    if duration_text:
        body_lines.append(duration_text)
    if job.maintenance_notes:
        body_lines.extend(["", "Remarks:", job.maintenance_notes])
    body_lines.extend(["", "Thank you."])
    body = "\n".join(body_lines)
    _send_email(f"Maintenance Job Completed: {job.job_code}", job.prod_email, body)

    return jsonify(job_schema.dump(job))


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
