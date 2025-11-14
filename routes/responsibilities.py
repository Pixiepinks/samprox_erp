from __future__ import annotations

from collections import defaultdict
import re
import html
import os
from datetime import date, timedelta
from typing import Iterable

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from marshmallow import ValidationError
from sqlalchemy import asc
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import requests
from requests import exceptions as requests_exceptions

from extensions import db
from models import (
    ResponsibilityAction,
    ResponsibilityDelegation,
    ResponsibilityPerformanceUnit,
    ResponsibilityRecurrence,
    ResponsibilityTask,
    ResponsibilityTaskStatus,
    RoleEnum,
    User,
)
from responsibility_performance import (
    calculate_metric,
    format_performance_value,
    unit_input_type,
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


def _resolve_performance_unit(task: ResponsibilityTask) -> ResponsibilityPerformanceUnit:
    unit = getattr(task, "perf_uom", ResponsibilityPerformanceUnit.PERCENTAGE_PCT)
    if isinstance(unit, ResponsibilityPerformanceUnit):
        return unit
    try:
        return ResponsibilityPerformanceUnit(unit)
    except (TypeError, ValueError):
        return ResponsibilityPerformanceUnit.PERCENTAGE_PCT


def _format_delegation_allocation(
    task: ResponsibilityTask, delegation: ResponsibilityDelegation
) -> str | None:
    try:
        value = getattr(delegation, "allocated_value", None)
    except AttributeError:
        value = None
    if value is None:
        return None
    unit = _resolve_performance_unit(task)
    try:
        return format_performance_value(unit, value)
    except Exception:  # pragma: no cover - defensive formatting
        return None


def _delegation_summary_lines(task: ResponsibilityTask) -> list[str]:
    lines: list[str] = []
    for delegation in getattr(task, "delegations", []) or []:
        delegate = getattr(delegation, "delegate", None)
        delegate_name = getattr(delegate, "name", None) or getattr(delegate, "email", None)
        if not delegate_name:
            delegate_name = "Delegated manager"
        allocation_display = _format_delegation_allocation(task, delegation)
        if allocation_display:
            lines.append(f"• {delegate_name} — {allocation_display}")
        else:
            lines.append(f"• {delegate_name}")
    return lines


def _first_name_from(name_hint: str | None, email: str | None) -> str:
    """Return a friendly first name for the greeting line."""

    if name_hint:
        parts = [part for part in name_hint.strip().split() if part]
        if parts:
            return parts[0]

    if email:
        local_part = email.split("@", 1)[0]
        pieces = [part for part in re.split(r"[-._\s]+", local_part) if part]
        if pieces:
            return pieces[0].title()

    return "there"


def _compose_responsibility_email(
    *,
    recipient: str,
    name_hint: str | None,
    verb: str,
    content_lines: list[str],
) -> str:
    """Return a formatted email body with greeting and inspirational closing."""

    first_name = _first_name_from(name_hint, recipient)
    greeting_line = f"Hi {first_name}, A new responsibility has been {verb} to you."

    body_lines = [greeting_line, ""]
    body_lines.extend(content_lines)
    body_lines.extend(
        [
            "",
            "Great achievements begin with clear responsibilities.",
            "Let’s make this task a success; I’ll be tracking your progress and milestones.",
            "Regards,",
            "Maximus — Your AICEO",
        ]
    )

    return "\n".join(body_lines)


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

    base_lines = [
        f"Responsibility No: {task.number}",
        f"Title: {task.title}",
        f"Scheduled for: {first_date}",
        f"Recurrence: {_render_recurrence(task)}",
        f"5D Action: {action_label}",
    ]

    if assignee_name:
        base_lines.append(f"Assigned to: {assignee_name}")
    else:
        base_lines.append("Assigned to: (not specified)")

    base_lines.append(f"Assigned by: {assigner_name}")

    overview_lines = ["Responsibility overview:"]
    overview_lines.extend(f"• {line}" for line in base_lines)

    extra_sections: list[str] = []

    if task.description:
        extra_sections.append("")
        extra_sections.append("Summary:")
        extra_sections.append(task.description.strip())

    if task.detail:
        extra_sections.append("")
        extra_sections.append(f"Detail: {task.detail.strip()}")

    if task.action_notes:
        extra_sections.append("")
        extra_sections.append(f"Notes: {task.action_notes.strip()}")

    base_content = list(overview_lines)
    base_content.extend(extra_sections)

    delegation_summary = _delegation_summary_lines(task)

    general_content = list(base_content)
    if delegation_summary:
        general_content.append("")
        general_content.append("Delegated allocations:")
        general_content.extend(delegation_summary)
    elif delegated_name:
        general_content.append("")
        general_content.append(f"Delegated to: {delegated_name}")

    recipients_sent: set[str] = set()

    def _send(
        recipient: str,
        lines: list[str],
        *,
        verb: str,
        name_hint: str | None = None,
    ) -> None:
        if not recipient or recipient in recipients_sent:
            return
        body = _compose_responsibility_email(
            recipient=recipient,
            name_hint=name_hint,
            verb=verb,
            content_lines=lines,
        )
        _deliver_responsibility_email(
            subject=f"New responsibility: {task.title}",
            recipient=recipient,
            body=body,
            context="responsibility notification",
        )
        recipients_sent.add(recipient)

    delegate_email_map: dict[str, User] = {}

    for delegation in getattr(task, "delegations", []) or []:
        delegate = getattr(delegation, "delegate", None)
        delegate_email = getattr(delegate, "email", None)
        if delegate_email:
            delegate_email_map[delegate_email] = delegate

    general_recipient = task.recipient_email
    general_name_hint = assignee_name
    general_verb = "assigned"

    if general_recipient in delegate_email_map:
        delegate_user = delegate_email_map[general_recipient]
        general_name_hint = getattr(delegate_user, "name", None)
        general_verb = "delegated"
    elif general_recipient == getattr(task.delegated_to, "email", None):
        general_name_hint = delegated_name
        general_verb = "delegated"
    elif general_recipient == getattr(task.assignee, "email", None):
        general_name_hint = assignee_name
    elif assignee_name:
        general_name_hint = assignee_name
    elif delegated_name:
        general_name_hint = delegated_name

    _send(
        general_recipient,
        general_content,
        verb=general_verb,
        name_hint=general_name_hint,
    )

    for delegation in getattr(task, "delegations", []) or []:
        delegate = getattr(delegation, "delegate", None)
        delegate_email = getattr(delegate, "email", None)
        if not delegate_email:
            continue
        delegate_content = list(base_content)
        allocation_display = _format_delegation_allocation(task, delegation)
        delegate_name = getattr(delegate, "name", None) or delegate_email
        delegate_content.append("")
        delegate_content.append("Delegated allocation assigned to you:")
        if allocation_display:
            delegate_content.append(f"• {delegate_name} — {allocation_display}")
        else:
            delegate_content.append(f"• {delegate_name}")
        _send(
            delegate_email,
            delegate_content,
            verb="delegated",
            name_hint=getattr(delegate, "name", None),
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
            parts.append(f"  • {task.title} ({assignee}) — {_render_recurrence(task)}")
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

    performance_unit_value = data.get("performance_unit")
    try:
        performance_unit = ResponsibilityPerformanceUnit(performance_unit_value)
    except ValueError:
        return jsonify({"errors": {"performanceUnit": ["Invalid unit of measure option."]}}), 422

    performance_responsible_value = data.get("performance_responsible")
    performance_actual_value = data.get("performance_actual")
    performance_metric_value = calculate_metric(
        performance_unit,
        performance_responsible_value,
        performance_actual_value,
    )
    performance_input_type = unit_input_type(performance_unit)

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

    delegations_payload = data.get("delegations") or []
    delegation_models: list[ResponsibilityDelegation] = []
    seen_delegates: set[int] = set()
    for entry in delegations_payload:
        delegate_id = entry.get("delegate_id")
        if delegate_id is None or delegate_id in seen_delegates:
            continue
        delegate = User.query.get(delegate_id)
        if not delegate:
            return jsonify({"msg": "Delegated manager was not found."}), 404
        seen_delegates.add(delegate_id)
        delegation_models.append(
            ResponsibilityDelegation(
                delegate_id=delegate.id,
                allocated_value=entry.get("allocated_value"),
            )
        )

    if not delegation_models and delegated_to is not None:
        delegation_models.append(ResponsibilityDelegation(delegate_id=delegated_to.id))

    recurrence = ResponsibilityRecurrence(data["recurrence"])
    status_value = data.get("status", ResponsibilityTaskStatus.PLANNED.value)
    status = ResponsibilityTaskStatus(status_value)
    action = ResponsibilityAction(data["action"])
    action_notes = data.get("action_notes")
    progress = _normalize_progress(data.get("progress"), action)

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
        perf_uom=performance_unit,
        perf_responsible_value=performance_responsible_value,
        perf_actual_value=performance_actual_value,
        perf_metric_value=performance_metric_value,
        perf_input_type=performance_input_type,
    )

    task.replace_delegations(delegation_models)

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

    performance_unit_value = data.get("performance_unit")
    try:
        performance_unit = ResponsibilityPerformanceUnit(performance_unit_value)
    except ValueError:
        return jsonify({"errors": {"performanceUnit": ["Invalid unit of measure option."]}}), 422

    performance_responsible_value = data.get("performance_responsible")
    performance_actual_value = data.get("performance_actual")
    performance_metric_value = calculate_metric(
        performance_unit,
        performance_responsible_value,
        performance_actual_value,
    )
    performance_input_type = unit_input_type(performance_unit)

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

    delegations_payload = data.get("delegations") or []
    delegation_models: list[ResponsibilityDelegation] = []
    seen_delegates: set[int] = set()
    for entry in delegations_payload:
        delegate_id = entry.get("delegate_id")
        if delegate_id is None or delegate_id in seen_delegates:
            continue
        delegate = User.query.get(delegate_id)
        if not delegate:
            return jsonify({"msg": "Delegated manager was not found."}), 404
        seen_delegates.add(delegate_id)
        delegation_models.append(
            ResponsibilityDelegation(
                delegate_id=delegate.id,
                allocated_value=entry.get("allocated_value"),
            )
        )

    if not delegation_models and delegated_to is not None:
        delegation_models.append(ResponsibilityDelegation(delegate_id=delegated_to.id))

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
    task.replace_delegations(delegation_models)
    task.update_custom_weekdays(data.get("custom_weekdays"))
    task.perf_uom = performance_unit
    task.perf_responsible_value = performance_responsible_value
    task.perf_actual_value = performance_actual_value
    task.perf_metric_value = performance_metric_value
    task.perf_input_type = performance_input_type

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

    query = (
        ResponsibilityTask.query.options(
            selectinload(ResponsibilityTask.assigner),
            selectinload(ResponsibilityTask.assignee),
            selectinload(ResponsibilityTask.delegated_to),
            selectinload(ResponsibilityTask.delegations).selectinload(ResponsibilityDelegation.delegate),
        )
        .order_by(
            asc(ResponsibilityTask.scheduled_for),
            asc(ResponsibilityTask.created_at),
        )
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

