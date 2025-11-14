from __future__ import annotations

from collections import defaultdict
import html
import os
from datetime import date, timedelta
from typing import Iterable

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from marshmallow import ValidationError
from sqlalchemy import asc
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import requests
from requests import exceptions as requests_exceptions

from extensions import db
from models import (
    ResponsibilityAction,
    ResponsibilityRecurrence,
    ResponsibilityTask,
    ResponsibilityTaskStatus,
    ResponsibilityPerformanceUnit,
    RoleEnum,
    User,
)
from responsibility_metrics import (
    format_value_for_display,
    get_unit_config,
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

RESEND_ENDPOINT = "https://api.resend.com/emails"
RESEND_SENDER = "Samprox ERP <no-reply@samprox.lk>"


def _send_email_via_resend(data: dict) -> None:
    api_key = os.environ["RESEND_API_KEY"]
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
    RoleEnum.outside_manager,
}


def _normalize_progress(value, action: ResponsibilityAction) -> int:
    """Return a clamped progress value honoring the selected action."""

    try:
        action_value = ResponsibilityAction(action)
    except (TypeError, ValueError):
        action_value = None

    if action_value in {ResponsibilityAction.DONE, ResponsibilityAction.DELETED}:
        return 100

    if value is None:
        return 0

    try:
        progress_value = int(value)
    except (TypeError, ValueError):
        return 0

    return max(0, min(100, progress_value))


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


class ResponsibilityEmailError(RuntimeError):
    """Raised when responsibility-related email delivery fails."""

    def __init__(self, user_message: str, original: Exception | None = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.original = original


def _deliver_responsibility_email(
    *,
    subject: str,
    recipient: str,
    body: str,
    context: str,
) -> None:
    """Send an email and raise :class:`ResponsibilityEmailError` on failure."""

    if not recipient:
        raise ResponsibilityEmailError("No recipient email address was provided.")

    html_body = html.escape(body).replace("\n", "<br>")
    data = {
        "from": RESEND_SENDER,
        "to": [recipient],
        "subject": subject,
        "html": html_body,
    }

    try:
        _send_email_via_resend(data)
    except KeyError as exc:  # pragma: no cover - configuration error
        current_app.logger.warning(
            "RESEND_API_KEY is not configured for %s emails.", context
        )
        raise ResponsibilityEmailError(
            f"Failed to send the {context} email: email service is not configured.",
            exc,
        ) from exc
    except requests_exceptions.Timeout as exc:
        current_app.logger.warning(
            "Failed to send %s email due to timeout: %s", context, exc, exc_info=exc
        )
        raise ResponsibilityEmailError(
            f"Failed to send the {context} email: the email service timed out.",
            exc,
        ) from exc
    except requests_exceptions.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code in {401, 403}:
            failure_message = (
                f"Failed to send the {context} email: authentication failed."
            )
        else:
            failure_message = (
                f"Failed to send the {context} email: the email service returned an error."
            )
        current_app.logger.warning(
            "Failed to send %s email: %s", context, exc, exc_info=exc
        )
        raise ResponsibilityEmailError(failure_message, exc) from exc
    except requests_exceptions.ConnectionError as exc:
        current_app.logger.warning(
            "Failed to send %s email due to connection error: %s",
            context,
            exc,
            exc_info=exc,
        )
        raise ResponsibilityEmailError(
            f"Failed to send the {context} email: the email service could not be reached.",
            exc,
        ) from exc
    except requests_exceptions.RequestException as exc:  # pragma: no cover - logging only
        current_app.logger.warning(
            "Failed to send %s email: %s", context, exc, exc_info=exc
        )
        raise ResponsibilityEmailError(
            f"Failed to send the {context} email.",
            exc,
        ) from exc


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

    _deliver_responsibility_email(
        subject=f"New responsibility: {task.title}",
        recipient=task.recipient_email,
        body="\n".join(lines),
        context="responsibility notification",
    )


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
            perf_unit = getattr(task, "perf_uom", ResponsibilityPerformanceUnit.PERCENTAGE_PCT)
            responsible = format_value_for_display(perf_unit, getattr(task, "perf_responsible_value", None))
            actual = format_value_for_display(perf_unit, getattr(task, "perf_actual_value", None))
            metric = format_value_for_display(
                ResponsibilityPerformanceUnit.PERCENTAGE_PCT,
                getattr(task, "perf_metric_value", None),
            )
            metrics_text = (
                f"Responsible: {responsible}; Actual: {actual}; Achievement: {metric}"
            )
            parts.append(
                f"  • {task.title} ({assignee}) — {_render_recurrence(task)} — {metrics_text}"
            )
        parts.append("")
    return "\n".join(parts).strip()


def send_weekly_email(
    recipient: str, start_date: date, subject: str | None, html: str
) -> dict[str, str]:
    subject_line = subject or f"Weekly Responsibility Plan ({start_date})"
    data = {
        "from": RESEND_SENDER,
        "to": [recipient],
        "subject": subject_line,
        "html": html,
    }

    try:
        response = requests.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
            json=data,
            timeout=15,
        )
        response.raise_for_status()
    except KeyError as error:
        current_app.logger.error(
            "Failed to send weekly plan email: RESEND_API_KEY is not set."
        )
        raise ResponsibilityEmailError(
            "Failed to send the weekly plan email: email service is not configured.",
            error,
        ) from error
    except requests_exceptions.Timeout as error:
        current_app.logger.error("Failed to send weekly plan email: %s", error)
        raise ResponsibilityEmailError(
            "Failed to send the weekly plan email: the email service timed out.",
            error,
        ) from error
    except requests_exceptions.HTTPError as error:
        current_app.logger.error("Failed to send weekly plan email: %s", error)
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if status_code in {401, 403}:
            message = "Failed to send the weekly plan email: authentication failed."
        else:
            message = "Failed to send the weekly plan email: the email service returned an error."
        raise ResponsibilityEmailError(message, error) from error
    except requests_exceptions.ConnectionError as error:
        current_app.logger.error("Failed to send weekly plan email: %s", error)
        raise ResponsibilityEmailError(
            "Failed to send the weekly plan email: the email service could not be reached.",
            error,
        ) from error
    except requests_exceptions.RequestException as error:
        current_app.logger.error("Failed to send weekly plan email: %s", error)
        raise ResponsibilityEmailError(
            "Failed to send the weekly plan email.",
            error,
        ) from error

    return {"status": "success", "message": "Weekly plan emailed successfully"}


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
    progress = _normalize_progress(data.get("progress"), action)

    perf_unit = ResponsibilityPerformanceUnit(data["perf_uom"])

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
        progress=progress,
        assigner_id=assigner_id,
        assignee_id=assignee.id if assignee else None,
        delegated_to_id=delegated_to.id if delegated_to else None,
        perf_uom=perf_unit,
        perf_responsible_value=data["perf_responsible_value"],
        perf_actual_value=data["perf_actual_value"],
        perf_metric_value=data["perf_metric_value"],
        perf_input_type=data.get("perf_input_type"),
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


@bp.put("/<int:task_id>")
@jwt_required()
def update_task(task_id: int):
    """Update an existing responsibility task."""

    role = _current_role()
    if role not in _ALLOWED_CREATOR_ROLES:
        return jsonify({"msg": "Only managers can update responsibility tasks."}), 403

    task = ResponsibilityTask.query.get(task_id)
    if not task:
        return jsonify({"msg": "Responsibility task was not found."}), 404

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
    if "status" in payload:
        status_value = data.get("status", ResponsibilityTaskStatus.PLANNED.value)
    else:
        current_status = getattr(task, "status", ResponsibilityTaskStatus.PLANNED)
        status_value = (
            current_status.value
            if isinstance(current_status, ResponsibilityTaskStatus)
            else ResponsibilityTaskStatus.PLANNED.value
        )
    status = ResponsibilityTaskStatus(status_value)
    action = ResponsibilityAction(data["action"])
    action_notes = data.get("action_notes")

    if "progress" in payload:
        progress_input = data.get("progress")
    else:
        progress_input = getattr(task, "progress", None)
    progress = _normalize_progress(progress_input, action)

    perf_unit = ResponsibilityPerformanceUnit(data["perf_uom"])

    task.title = data["title"]
    task.description = data.get("description")
    task.detail = data.get("detail")
    task.scheduled_for = data["scheduled_for"]
    task.recurrence = recurrence
    task.status = status
    task.action = action
    task.action_notes = action_notes
    task.recipient_email = data["recipient_email"]
    task.progress = progress
    task.assignee_id = assignee.id if assignee else None
    task.delegated_to_id = delegated_to.id if delegated_to else None
    task.update_custom_weekdays(data.get("custom_weekdays"))
    task.perf_uom = perf_unit
    task.perf_responsible_value = data["perf_responsible_value"]
    task.perf_actual_value = data["perf_actual_value"]
    task.perf_metric_value = data["perf_metric_value"]
    task.perf_input_type = data.get("perf_input_type")

    error_message = "Unable to update responsibility. Please try again."
    try:
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.exception("Failed to update responsibility task.", exc_info=error)
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
    return jsonify(response), 200


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
    html_summary = html.escape(summary).replace("\n", "<br>")
    try:
        result = send_weekly_email(recipient, start_date, subject, html_summary)
    except ResponsibilityEmailError as error:
        return jsonify({"msg": error.user_message}), 500

    total_occurrences = sum(len(items) for items in schedule.values())
    response = {
        **result,
        "startDate": start_date.isoformat(),
        "endDate": (start_date + timedelta(days=6)).isoformat(),
        "occurrenceCount": total_occurrences,
    }
    return jsonify(response), 200

