from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from marshmallow import ValidationError
from sqlalchemy import asc
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db, mail
from flask_mail import Message
from models import (
    ResponsibilityAction,
    ResponsibilityRecurrence,
    ResponsibilityTask,
    ResponsibilityTaskStatus,
    RoleEnum,
    User,
)
from schemas import (
    ResponsibilityTaskCreateSchema,
    ResponsibilityTaskSchema,
    UserSchema,
    describe_responsibility_recurrence,
)

bp = Blueprint("responsibilities", __name__, url_prefix="/api/responsibilities")

task_schema = ResponsibilityTaskSchema()
tasks_schema = ResponsibilityTaskSchema(many=True)
task_create_schema = ResponsibilityTaskCreateSchema()
users_schema = UserSchema(many=True)


def _is_responsibility_number_conflict(error: IntegrityError) -> bool:
    """Return ``True`` when ``error`` refers to the responsibility number constraint."""

    message = str(getattr(error, "orig", "") or "")
    if not message:
        message = str(error)
    message = message.lower()
    return "responsibility" in message and "number" in message

_ALLOWED_CREATOR_ROLES = {
    RoleEnum.production_manager,
    RoleEnum.maintenance_manager,
    RoleEnum.admin,
}


def _current_role() -> RoleEnum | None:
    try:
        claims = get_jwt()
    except RuntimeError:
        return None
    try:
        return RoleEnum(claims.get("role"))
    except (ValueError, TypeError):
        return None


def _current_user_id() -> int | None:
    try:
        claims = get_jwt()
    except RuntimeError:
        return None
    try:
        return int(claims.get("sub"))
    except (TypeError, ValueError):
        return None


@bp.get("/assignees")
@jwt_required()
def list_assignees():
    """Return active managers that can be assigned responsibilities."""

    role = _current_role()
    if role not in _ALLOWED_CREATOR_ROLES:
        return jsonify({"msg": "Only managers can access responsibility planning."}), 403

    manager_roles = [
        RoleEnum.production_manager,
        RoleEnum.maintenance_manager,
        RoleEnum.finance_manager,
        RoleEnum.admin,
    ]

    managers = (
        User.query.filter(User.role.in_(manager_roles), User.active.is_(True))
        .order_by(asc(User.name))
        .all()
    )
    return jsonify(users_schema.dump(managers))


def _render_recurrence(task: ResponsibilityTask) -> str:
    label = describe_responsibility_recurrence(task)
    return label or "No recurrence"


def _send_task_email(task: ResponsibilityTask) -> None:
    assigner_name = getattr(task.assigner, "name", "A manager")
    assignee_name = getattr(task.assignee, "name", None)
    delegated_name = getattr(task.delegated_to, "name", None)
    first_date = task.scheduled_for.strftime("%B %d, %Y") if task.scheduled_for else "N/A"
    action = getattr(task, "action", ResponsibilityAction.DONE)
    try:
        action_label = ResponsibilityAction(action).value.replace("_", " ").title()
    except Exception:
        action_label = "—"

    lines = [
        f"Responsibility No: {task.number}",
        f"Title: {task.title}",
        f"Scheduled for: {first_date}",
        f"Recurrence: {_render_recurrence(task)}",
        f"5D Action: {action_label}",
    ]

    if assignee_name:
        lines.append(f"Assigned to: {assignee_name}")
    else:
        lines.append("Assigned to: (not specified)")

    if delegated_name:
        lines.append(f"Delegated to: {delegated_name}")

    lines.append(f"Assigned by: {assigner_name}")

    if task.description:
        lines.append("")
        lines.append(task.description.strip())

    if task.detail:
        lines.append("")
        lines.append(f"Detail: {task.detail.strip()}")

    if task.action_notes:
        lines.append("")
        lines.append(f"Notes: {task.action_notes.strip()}")

    message = Message(
        subject=f"New responsibility: {task.title}",
        recipients=[task.recipient_email],
        body="\n".join(lines),
    )

    mail.send(message)


def _occurrences_for_week(tasks: Iterable[ResponsibilityTask], start: date) -> dict[date, list[ResponsibilityTask]]:
    schedule: dict[date, list[ResponsibilityTask]] = defaultdict(list)
    end = start + timedelta(days=6)
    current = start
    while current <= end:
        for task in tasks:
            if task.occurs_on(current):
                schedule[current].append(task)
        current += timedelta(days=1)
    return schedule


def _format_weekly_summary(schedule: dict[date, list[ResponsibilityTask]]) -> str:
    if not schedule:
        return "No responsibilities are scheduled for the selected week."

    parts: list[str] = []
    for day in sorted(schedule):
        formatted_day = day.strftime("%A, %B %d")
        parts.append(formatted_day)
        for task in schedule[day]:
            assignee = getattr(task.assignee, "name", "Unassigned")
            parts.append(f"  • {task.title} ({assignee}) — {_render_recurrence(task)}")
        parts.append("")
    return "\n".join(parts).strip()


@bp.post("")
@jwt_required()
def create_task():
    """Create a new responsibility task and send a notification."""

    role = _current_role()
    if role not in _ALLOWED_CREATOR_ROLES:
        return jsonify({"msg": "Only managers can create responsibility tasks."}), 403

    assigner_id = _current_user_id()
    if assigner_id is None:
        return jsonify({"msg": "Invalid authentication token."}), 422

    payload = request.get_json(silent=True) or {}
    try:
        data = task_create_schema.load(payload)
    except ValidationError as error:
        return jsonify({"errors": error.normalized_messages()}), 422

    assignee = None
    assignee_id = data.get("assignee_id")
    if assignee_id is not None:
        assignee = User.query.get(assignee_id)
        if not assignee:
            return jsonify({"msg": "Assigned manager was not found."}), 404

    delegated_to = None
    delegated_to_id = data.get("delegated_to_id")
    if delegated_to_id is not None:
        delegated_to = User.query.get(delegated_to_id)
        if not delegated_to:
            return jsonify({"msg": "Delegated manager was not found."}), 404

    recurrence = ResponsibilityRecurrence(data["recurrence"])
    status_value = data.get("status", ResponsibilityTaskStatus.PLANNED.value)
    status = ResponsibilityTaskStatus(status_value)
    action = ResponsibilityAction(data["action"])
    action_notes = data.get("action_notes")

    task = ResponsibilityTask(
        title=data["title"],
        description=data.get("description"),
        detail=data.get("detail"),
        scheduled_for=data["scheduled_for"],
        recurrence=recurrence,
        status=status,
        action=action,
        action_notes=action_notes,
        recipient_email=data["recipient_email"],
        assigner_id=assigner_id,
        assignee_id=assignee.id if assignee else None,
        delegated_to_id=delegated_to.id if delegated_to else None,
    )

    task.update_custom_weekdays(data.get("custom_weekdays"))

    db.session.add(task)

    error_message = "Unable to save responsibility. Please try again."
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        if _is_responsibility_number_conflict(error):
            current_app.logger.warning(
                "Responsibility number conflict detected; retrying assignment.",
                exc_info=error,
            )
            task.number = None
            db.session.add(task)
            try:
                db.session.flush()
                db.session.commit()
            except (IntegrityError, SQLAlchemyError) as retry_error:
                db.session.rollback()
                current_app.logger.exception(
                    "Failed to allocate a unique responsibility number after retry.",
                    exc_info=retry_error,
                )
                return jsonify({"msg": error_message}), 500
        else:
            current_app.logger.exception(
                "Failed to save responsibility task.",
                exc_info=error,
            )
            return jsonify({"msg": error_message}), 500

    notification = {"sent": True}
    try:
        _send_task_email(task)
    except Exception as error:  # pragma: no cover - defensive logging
        current_app.logger.exception("Failed to send responsibility email")
        notification = {
            "sent": False,
            "message": str(error),
        }

    response = task_schema.dump(task)
    response["email_notification"] = notification
    return jsonify(response), 201


@bp.get("")
@jwt_required()
def list_tasks():
    """Return responsibility tasks ordered by start date and creation time."""

    role = _current_role()
    if role not in _ALLOWED_CREATOR_ROLES:
        return jsonify({"msg": "Only managers can view responsibility tasks."}), 403

    query = ResponsibilityTask.query.order_by(
        asc(ResponsibilityTask.scheduled_for),
        asc(ResponsibilityTask.created_at),
    )

    assignee_id = request.args.get("assigneeId")
    if assignee_id:
        try:
            assignee_value = int(assignee_id)
        except ValueError:
            return jsonify({"msg": "Invalid assignee filter."}), 422
        query = query.filter(ResponsibilityTask.assignee_id == assignee_value)

    tasks = query.all()
    return jsonify(tasks_schema.dump(tasks))


@bp.post("/send-weekly")
@jwt_required()
def send_weekly_plan():
    """Send a weekly summary of responsibilities to a recipient."""

    role = _current_role()
    if role not in _ALLOWED_CREATOR_ROLES:
        return jsonify({"msg": "Only managers can send weekly plans."}), 403

    payload = request.get_json(silent=True) or {}
    recipient = payload.get("recipientEmail")
    if not recipient:
        return jsonify({"msg": "recipientEmail is required."}), 422

    start_value = payload.get("startDate")
    if start_value:
        try:
            start_date = date.fromisoformat(start_value)
        except ValueError:
            return jsonify({"msg": "startDate must be YYYY-MM-DD."}), 422
    else:
        today = date.today()
        start_date = today - timedelta(days=today.weekday())

    tasks = ResponsibilityTask.query.order_by(asc(ResponsibilityTask.scheduled_for)).all()
    schedule = _occurrences_for_week(tasks, start_date)
    summary = _format_weekly_summary(schedule)

    subject = f"Responsibility plan for week starting {start_date.strftime('%B %d, %Y')}"
    message = Message(subject=subject, recipients=[recipient], body=summary)

    mail.send(message)

    total_occurrences = sum(len(items) for items in schedule.values())
    response = {
        "sent": True,
        "startDate": start_date.isoformat(),
        "endDate": (start_date + timedelta(days=6)).isoformat(),
        "occurrenceCount": total_occurrences,
    }
    return jsonify(response), 200

