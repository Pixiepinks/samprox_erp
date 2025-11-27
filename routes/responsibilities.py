from __future__ import annotations

from collections import defaultdict
import random
import re
import html
import os
from datetime import date, datetime, timedelta
from typing import Iterable

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_jwt_extended import get_jwt, jwt_required
from marshmallow import ValidationError
from sqlalchemy import asc, or_
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
    TeamMember,
    User,
)
from responsibility_performance import (
    calculate_metric,
    format_metric,
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
RESEND_DEFAULT_SENDER = "Samprox ERP <no-reply@samprox.lk>"

MOTIVATIONAL_MESSAGES: tuple[str, ...] = (
    "Great achievements begin with clear responsibilities. Let’s make this task a success!",
    "Every great outcome starts with ownership — lead this one with confidence and focus.",
    "Your leadership in this responsibility will shape the results we’re aiming for.",
    "Small steps done with consistency create big outcomes — let’s start strong!",
    "Clear goals lead to powerful results — make this responsibility count.",
    "Excellence begins with accountability — and this task is your next opportunity to shine.",
    "Success follows those who take charge — let’s make this a milestone to remember.",
    "Every responsibility is a chance to grow, improve, and achieve something greater.",
    "You’ve got the skill and the drive — now let’s see your impact in action.",
    "Your dedication is the foundation of progress — let’s build success together.",
    "Big goals are achieved through small wins — start with this one.",
    "The future belongs to those who take responsibility — lead the way!",
    "Great teams grow through great ownership — thank you for taking this on.",
    "Success doesn’t happen by chance, it happens by commitment — and you’ve got it.",
    "Responsibility isn’t a burden, it’s a mark of trust — and you’ve earned it.",
    "Every task well done adds to our collective success — let’s make this one exceptional.",
    "When responsibility meets action, results follow — let’s make it happen!",
    "Your initiative makes the difference — lead this with purpose and passion.",
    "Accountability is the seed of progress — nurture it with excellence.",
    "You are trusted with this responsibility because you make things happen — let’s do it again!",
)


_PERFORMANCE_LABEL_OVERRIDES: dict[str, str] = {
    "quantity_based": "Quantity-based",
    "qty": "Quantity (Qty)",
    "amount_lkr": "Amount (LKR)",
    "kg": "Kilograms (kg)",
    "kwh": "kWh",
    "rpm": "RPM",
    "quality_metric": "Quality Metrics",
    "percentage_pct": "Percentage (%)",
    "margin_pct": "Margin (%)",
    "error_rate_pct": "Error Rate (%)",
    "success_rate_pct": "Success Rate (%)",
    "accuracy_pct": "Accuracy (%)",
    "compliance_pct": "Compliance (%)",
    "conversion_pct": "Conversion (%)",
    "completion_pct": "Completion (%)",
    "sla_pct": "SLA (%)",
    "time_per_unit": "Time per Unit",
}


def _performance_unit_label(unit: str | None) -> str | None:
    if not unit:
        return None
    normalized = str(unit).strip().lower()
    if not normalized:
        return None
    override = _PERFORMANCE_LABEL_OVERRIDES.get(normalized)
    if override:
        return override
    words = [
        word.capitalize() if word else ""
        for word in normalized.replace("-", " ").replace("_", " ").split()
    ]
    if not words:
        return None
    return " ".join(words)


def _format_progress_label(progress: object) -> str | None:
    if progress is None:
        return None
    if isinstance(progress, bool):
        return None
    if isinstance(progress, (int, float)):
        value = float(progress)
        if value.is_integer():
            return f"{int(value)}%"
        rounded = round(value, 1)
        if rounded.is_integer():
            return f"{int(rounded)}%"
        return f"{rounded}%"
    text = str(progress).strip()
    if not text:
        return None
    if text.endswith("%"):
        return text
    return f"{text}%"


def _normalize_member_name(member: TeamMember | None) -> str | None:
    if member is None:
        return None
    name = getattr(member, "name", None)
    if isinstance(name, str):
        stripped = name.strip()
        if stripped:
            return stripped
    return None


def _normalize_user_name(user: User | None) -> str | None:
    if user is None:
        return None
    name = getattr(user, "name", None)
    if isinstance(name, str):
        stripped = name.strip()
        if stripped:
            return stripped
    return None


def _task_assignee_name(task: ResponsibilityTask) -> str | None:
    member_name = _normalize_member_name(getattr(task, "assignee_member", None))
    if member_name:
        return member_name
    return _normalize_user_name(getattr(task, "assignee", None))


def _task_delegated_name(task: ResponsibilityTask) -> str | None:
    member_name = _normalize_member_name(getattr(task, "delegated_to_member", None))
    if member_name:
        return member_name
    delegated = getattr(task, "delegated_to", None)
    delegations = getattr(task, "delegations", None) or []
    if delegations:
        first = delegations[0]
        fallback_member = _normalize_member_name(getattr(first, "delegate_member", None))
        if fallback_member:
            return fallback_member
    for delegation in delegations[1:]:
        delegation_member = getattr(delegation, "delegate_member", None)
        member_name = _normalize_member_name(delegation_member)
        if member_name:
            return member_name
    user_name = _normalize_user_name(delegated)
    if user_name:
        return user_name
    if delegations:
        first = delegations[0]
        fallback_user = _normalize_user_name(getattr(first, "delegate", None))
        if fallback_user:
            return fallback_user
        delegate_user = getattr(first, "delegate", None)
        email = getattr(delegate_user, "email", None)
        if isinstance(email, str):
            stripped = email.strip()
            if stripped:
                return stripped
    for delegation in delegations[1:]:
        delegate_user = getattr(delegation, "delegate", None)
        user_name = _normalize_user_name(delegate_user)
        if user_name:
            return user_name
    for delegation in delegations[1:]:
        delegate_user = getattr(delegation, "delegate", None)
        email = getattr(delegate_user, "email", None)
        if isinstance(email, str):
            stripped = email.strip()
            if stripped:
                return stripped
    email = getattr(delegated, "email", None)
    if isinstance(email, str):
        stripped = email.strip()
        if stripped:
            return stripped
    return None


def _decorate_task_names(task: ResponsibilityTask) -> dict[str, str | None]:
    """Return a mapping with decorated assignee and delegate names."""

    return {
        "assigneeName": _task_assignee_name(task),
        "delegatedToName": _task_delegated_name(task),
    }


def _task_team_members(task: ResponsibilityTask) -> list[TeamMember]:
    """Return the team members directly responsible for ``task``."""

    members: list[TeamMember] = []
    seen_ids: set[int] = set()

    for delegation in getattr(task, "delegations", []) or []:
        delegate_member = getattr(delegation, "delegate_member", None)
        member_id = getattr(delegate_member, "id", None)
        if member_id is None:
            continue
        try:
            member_id = int(member_id)
        except (TypeError, ValueError):
            continue
        if member_id in seen_ids:
            continue
        members.append(delegate_member)
        seen_ids.add(member_id)

    delegated_member = getattr(task, "delegated_to_member", None)
    delegated_member_id = getattr(delegated_member, "id", None)
    if delegated_member_id is not None:
        try:
            delegated_member_id = int(delegated_member_id)
        except (TypeError, ValueError):
            delegated_member_id = None
    if delegated_member_id is not None and delegated_member_id not in seen_ids:
        members.append(delegated_member)
        seen_ids.add(delegated_member_id)

    assignee_member = getattr(task, "assignee_member", None)
    assignee_member_id = getattr(assignee_member, "id", None)
    if assignee_member_id is not None:
        try:
            assignee_member_id = int(assignee_member_id)
        except (TypeError, ValueError):
            assignee_member_id = None
    if assignee_member_id is not None and assignee_member_id not in seen_ids:
        members.append(assignee_member)

    return [member for member in members if isinstance(member, TeamMember)]


def _delegation_display_name(delegation: ResponsibilityDelegation) -> str:
    member_name = _normalize_member_name(getattr(delegation, "delegate_member", None))
    if member_name:
        return member_name
    user = getattr(delegation, "delegate", None)
    user_name = _normalize_user_name(user)
    if user_name:
        return user_name
    email = getattr(user, "email", None)
    if isinstance(email, str) and email.strip():
        return email.strip()
    return "Delegated assignee"


def _resolve_assignment_target(
    candidate_id: int | None, *, preferred: str | None = None
) -> tuple[User | None, TeamMember | None]:
    if candidate_id is None:
        return (None, None)

    preference = (preferred or "").strip().lower().replace("-", "_")

    if preference in {"team_member", "team"}:
        member = TeamMember.query.get(candidate_id)
        if member is not None:
            return (None, member)
        user = User.query.get(candidate_id)
        if user is not None:
            return (user, None)
        return (None, None)

    if preference in {"user", "manager"}:
        user = User.query.get(candidate_id)
        if user is not None:
            return (user, None)
        member = TeamMember.query.get(candidate_id)
        if member is not None:
            return (None, member)
        return (None, None)

    user = User.query.get(candidate_id)
    if user is not None:
        return (user, None)

    member = TeamMember.query.get(candidate_id)
    if member is not None:
        return (None, member)

    return (None, None)


def _resolve_resend_sender() -> str:
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
    RoleEnum.finance_manager,
    RoleEnum.admin,
    RoleEnum.outside_manager,
}

_REPORT_ALLOWED_ROLES = set(RoleEnum)


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


def _normalize_recipients(addresses: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
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


def _split_email_addresses(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[;,]", value)
    return _normalize_recipients(parts)


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
        delegate_name = _delegation_display_name(delegation)
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
    greeting_lines = [
        f"Hi {first_name},",
        f"A new responsibility has been {verb} to you.",
    ]

    body_lines = greeting_lines + [""]
    body_lines.extend(content_lines)
    motivational_line = random.choice(MOTIVATIONAL_MESSAGES)
    body_lines.extend(
        [
            "",
            motivational_line,
            "",
            "Regards,",
            "Maximus — Your AICEO",
        ]
    )

    return "\n".join(body_lines)


def _format_date(value: date | None) -> str:
    if not isinstance(value, date):
        return "—"
    return value.strftime("%B %d, %Y")


def _format_action_label(action: ResponsibilityAction | str | None) -> str:
    try:
        if isinstance(action, ResponsibilityAction):
            normalized = action
        else:
            normalized = ResponsibilityAction(action)
        return normalized.value.replace("_", " ").title()
    except Exception:
        return "—"


def _format_custom_weekdays(task: ResponsibilityTask) -> str:
    weekdays = getattr(task, "custom_weekday_list", None) or []
    if not weekdays:
        return "—"
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    labels: list[str] = []
    for index in weekdays:
        try:
            labels.append(names[int(index)])
        except (IndexError, TypeError, ValueError):
            continue
    return ", ".join(labels) if labels else "—"


@bp.get("/assignees")
@jwt_required()
def list_assignees():
    """Return active managers that can be assigned responsibilities."""

    role = _current_role()
    if role not in _ALLOWED_CREATOR_ROLES:
        return jsonify({"msg": "You are not authorized to access responsibility planning."}), 403

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
    cc: Iterable[str] | None = None,
) -> None:
    """Send an email and raise :class:`ResponsibilityEmailError` on failure."""

    if not recipient:
        raise ResponsibilityEmailError("No recipient email address was provided.")

    cleaned_recipient = recipient.strip()
    if not cleaned_recipient:
        raise ResponsibilityEmailError("No recipient email address was provided.")

    html_body = html.escape(body).replace("\n", "<br>")

    cc_values = _normalize_recipients(cc or [])
    filtered_cc: list[str] = []
    recipient_set = {cleaned_recipient.lower()}
    for address in cc_values:
        lowered = address.lower()
        if lowered in recipient_set:
            continue
        filtered_cc.append(address)
        recipient_set.add(lowered)

    default_bcc = current_app.config.get("MAIL_DEFAULT_BCC", [])
    if isinstance(default_bcc, str):
        default_bcc = [default_bcc]
    bcc_values = _normalize_recipients(default_bcc or [])
    filtered_bcc = [
        address
        for address in bcc_values
        if address.lower() not in recipient_set
    ]
    recipient_set.update(address.lower() for address in filtered_bcc)

    data = {
        "from": _resolve_resend_sender(),
        "to": [cleaned_recipient],
        "subject": subject,
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
    assigner_name = _normalize_user_name(getattr(task, "assigner", None)) or "A manager"
    assignee_name = _task_assignee_name(task)
    delegated_name = _task_delegated_name(task)
    first_date = task.scheduled_for.strftime("%B %d, %Y") if task.scheduled_for else "N/A"
    action = getattr(task, "action", ResponsibilityAction.DONE)
    try:
        action_label = ResponsibilityAction(action).value.replace("_", " ").title()
    except Exception:
        action_label = "—"

    unit = _resolve_performance_unit(task)
    unit_label = _performance_unit_label(
        unit.value if isinstance(unit, ResponsibilityPerformanceUnit) else unit
    )
    unit_display = (
        unit_label
        or (unit.value if isinstance(unit, ResponsibilityPerformanceUnit) else str(unit))
        or "—"
    )

    responsible_value = format_performance_value(
        unit, getattr(task, "perf_responsible_value", None)
    )
    actual_value = format_performance_value(
        unit, getattr(task, "perf_actual_value", None)
    )
    metric_value = format_metric(unit, getattr(task, "perf_metric_value", None))
    progress_label = _format_progress_label(getattr(task, "progress", None))

    base_lines = [
        f"Responsibility No: {task.number}",
        f"Title: {task.title}",
        f"Scheduled for: {first_date}",
        f"5D Action: {action_label}",
        f"Unit of measure: {unit_display}",
        f"Responsible: {responsible_value if responsible_value is not None else '—'}",
        f"Actual: {actual_value if actual_value is not None else '—'}",
        f"Performance metric: {metric_value if metric_value is not None else '—'}",
    ]

    if assignee_name:
        base_lines.append(f"Assigned to: {assignee_name}")
    else:
        base_lines.append("Assigned to: (not specified)")

    base_lines.append(f"Assigned by: {assigner_name}")

    if progress_label:
        base_lines.append(f"Progress: {progress_label}")

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
    cc_addresses = _split_email_addresses(getattr(task, "cc_email", None))

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
            cc=cc_addresses,
        )
        recipients_sent.add(recipient)

    delegate_email_map: dict[str, str | None] = {}

    for delegation in getattr(task, "delegations", []) or []:
        delegate_user = getattr(delegation, "delegate", None)
        delegate_email = getattr(delegate_user, "email", None)
        if isinstance(delegate_email, str) and delegate_email.strip():
            delegate_email_map[delegate_email.strip()] = _normalize_user_name(
                delegate_user
            )

        delegate_member = getattr(delegation, "delegate_member", None)
        member_email = getattr(delegate_member, "email", None)
        if isinstance(member_email, str) and member_email.strip():
            delegate_email_map[member_email.strip()] = _normalize_member_name(
                delegate_member
            )

    general_recipient = task.recipient_email
    general_name_hint = assignee_name
    general_verb = "assigned"

    if general_recipient in delegate_email_map:
        general_name_hint = delegate_email_map[general_recipient] or delegated_name
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


def _delegation_change_key(task: ResponsibilityTask) -> tuple[tuple[str | None, int | None, object], ...]:
    entries: list[tuple[str | None, int | None, object]] = []
    for delegation in getattr(task, "delegations", []) or []:
        delegate_member_id = getattr(delegation, "delegate_member_id", None)
        delegate_id = getattr(delegation, "delegate_id", None)
        allocation = getattr(delegation, "allocated_value", None)
        if delegate_member_id is not None:
            try:
                entries.append(("member", int(delegate_member_id), allocation))
            except (TypeError, ValueError):
                entries.append(("member", None, allocation))
        elif delegate_id is not None:
            try:
                entries.append(("user", int(delegate_id), allocation))
            except (TypeError, ValueError):
                entries.append(("user", None, allocation))
        else:
            entries.append((None, None, allocation))
    entries.sort(key=lambda value: (value[0] or "", value[1] or 0))
    return tuple(entries)


def _format_delegation_summary(task: ResponsibilityTask) -> str:
    lines = _delegation_summary_lines(task)
    cleaned = [line.lstrip("• ").strip() for line in lines if line.strip()]
    return "; ".join(cleaned) if cleaned else "—"


def _task_change_state(task: ResponsibilityTask) -> dict[str, tuple[object, str]]:
    unit = _resolve_performance_unit(task)
    action = getattr(task, "action", None)
    progress = getattr(task, "progress", None)
    responsible = getattr(task, "perf_responsible_value", None)
    actual = getattr(task, "perf_actual_value", None)
    metric = getattr(task, "perf_metric_value", None)
    scheduled_for = getattr(task, "scheduled_for", None)
    recurrence = getattr(task, "recurrence", None)
    custom_weekdays = list(getattr(task, "custom_weekday_list", None) or [])

    if getattr(task, "assignee_member_id", None) is not None:
        assignee_key: tuple[str, int | None] = ("member", getattr(task, "assignee_member_id", None))
    elif getattr(task, "assignee_id", None) is not None:
        assignee_key = ("user", getattr(task, "assignee_id", None))
    else:
        assignee_key = (None, None)

    delegations = _delegation_change_key(task)

    detail = (getattr(task, "detail", None) or "").strip()
    notes = (getattr(task, "action_notes", None) or "").strip()

    progress_label = _format_progress_label(progress) or "—"
    unit_label = _performance_unit_label(
        unit.value if isinstance(unit, ResponsibilityPerformanceUnit) else unit
    )
    responsible_display = format_performance_value(unit, responsible) or "—"
    actual_display = format_performance_value(unit, actual) or "—"
    metric_display = format_metric(unit, metric) or "—"

    return {
        "action": (action, _format_action_label(action)),
        "progress": (progress, progress_label),
        "unit": (unit, unit_label or "—"),
        "responsible": (responsible, responsible_display),
        "actual": (actual, actual_display),
        "metric": (metric, metric_display),
        "scheduled_for": (scheduled_for, _format_date(scheduled_for)),
        "recurrence": (recurrence, _render_recurrence(task) or "—"),
        "custom_weekdays": (tuple(custom_weekdays), _format_custom_weekdays(task)),
        "assignee": (assignee_key, _task_assignee_name(task) or "—"),
        "delegations": (delegations, _format_delegation_summary(task)),
        "detail": (detail, detail or "—"),
        "notes": (notes, notes or "—"),
    }


def _collect_task_changes(
    before: dict[str, tuple[object, str]], after: dict[str, tuple[object, str]]
) -> list[dict[str, str]]:
    fields = [
        ("action", "5D Action"),
        ("progress", "Progress"),
        ("unit", "Unit of measure"),
        ("responsible", "Responsible target"),
        ("actual", "Actual"),
        ("metric", "Performance metric"),
        ("scheduled_for", "First scheduled date"),
        ("recurrence", "Recurrence"),
        ("custom_weekdays", "Custom weekdays"),
        ("assignee", "Assigned to"),
        ("delegations", "Delegated team members"),
        ("detail", "Detail"),
        ("notes", "Notes"),
    ]
    changes: list[dict[str, str]] = []
    for key, label in fields:
        before_raw, before_display = before.get(key, (None, "—"))
        after_raw, after_display = after.get(key, (None, "—"))
        if before_raw == after_raw:
            continue
        changes.append(
            {
                "label": label,
                "old": before_display or "—",
                "new": after_display or "—",
            }
        )
    return changes


def _send_task_update_email(
    task: ResponsibilityTask,
    *,
    changes: list[dict[str, str]],
    detail_changed: bool,
    notes_changed: bool,
    updated_by: str,
) -> None:
    if not changes:
        return

    unit = _resolve_performance_unit(task)
    unit_label = _performance_unit_label(
        unit.value if isinstance(unit, ResponsibilityPerformanceUnit) else unit
    )
    responsible_display = format_performance_value(
        unit, getattr(task, "perf_responsible_value", None)
    )
    actual_display = format_performance_value(
        unit, getattr(task, "perf_actual_value", None)
    )

    progress_label = _format_progress_label(getattr(task, "progress", None)) or "—"

    assignee_name = _task_assignee_name(task) or _task_delegated_name(task)
    subject = f"Responsibility updated: {task.title} (No. {task.number})"

    motivational_line = random.choice(MOTIVATIONAL_MESSAGES)

    body = render_template(
        "responsibility_updated_email.html",
        assignee_name=assignee_name,
        task_number=task.number,
        title=task.title,
        scheduled_for=_format_date(getattr(task, "scheduled_for", None)),
        action=_format_action_label(getattr(task, "action", None)),
        unit=unit_label or "—",
        responsible_target=responsible_display or "—",
        actual_value=actual_display or "—",
        progress=progress_label,
        changes=changes,
        detail_updated=detail_changed,
        detail_text=(getattr(task, "detail", None) or "").strip(),
        notes_updated=notes_changed,
        notes_text=(getattr(task, "action_notes", None) or "").strip(),
        updated_by=updated_by or "A manager",
        motivation=motivational_line,
    )

    _deliver_responsibility_email(
        subject=subject,
        recipient=getattr(task, "recipient_email", None),
        body=body,
        context="responsibility update notification",
        cc=_split_email_addresses(getattr(task, "cc_email", None)),
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


def _occurrences_for_range(
    tasks: Iterable[ResponsibilityTask], start: date, end: date
) -> list[tuple[date, ResponsibilityTask]]:
    """Return ``(date, task)`` tuples for occurrences in ``[start, end]``."""

    occurrences: list[tuple[date, ResponsibilityTask]] = []
    current = start
    while current <= end:
        for task in tasks:
            if task.occurs_on(current):
                occurrences.append((current, task))
        current += timedelta(days=1)
    return occurrences


def _format_weekly_summary(schedule: dict[date, list[ResponsibilityTask]]) -> str:
    if not schedule:
        return "No responsibilities are scheduled for the selected week."

    parts: list[str] = []
    for day in sorted(schedule):
        formatted_day = day.strftime("%A, %B %d")
        parts.append(formatted_day)
        for task in schedule[day]:
            assignee_name = _task_assignee_name(task) or "Unassigned"
            parts.append(f"  • {task.title} ({assignee_name}) — {_render_recurrence(task)}")
        parts.append("")
    return "\n".join(parts).strip()


def send_weekly_email(
    recipient: str, start_date: date, subject: str | None, html: str
) -> dict[str, str]:
    subject_line = subject or f"Weekly Responsibility Plan ({start_date})"
    cleaned_recipient = recipient.strip()
    data = {
        "from": _resolve_resend_sender(),
        "to": [cleaned_recipient],
        "subject": subject_line,
        "html": html,
    }
    default_bcc = current_app.config.get("MAIL_DEFAULT_BCC", [])
    if isinstance(default_bcc, str):
        default_bcc = [default_bcc]
    bcc_values = _normalize_recipients(default_bcc or [])
    filtered_bcc = [
        address
        for address in bcc_values
        if address.lower() != cleaned_recipient.lower()
    ]
    if filtered_bcc:
        data["bcc"] = filtered_bcc

    try:
        response = requests.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {_resend_api_key()}",
                "Content-Type": "application/json",
            },
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
        return jsonify({"msg": "You are not authorized to create responsibility tasks."}), 403

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

    assignee_user: User | None = None
    assignee_member: TeamMember | None = None
    assignee_id = data.get("assignee_id")
    if assignee_id is not None:
        assignee_user, assignee_member = _resolve_assignment_target(
            assignee_id, preferred=data.get("assignee_type")
        )
        if assignee_user is None and assignee_member is None:
            return jsonify({"msg": "Selected assignee was not found."}), 404

    delegated_to_user: User | None = None
    delegated_to_member: TeamMember | None = None
    delegated_to_id = data.get("delegated_to_id")
    if delegated_to_id is not None:
        preferred_delegate_type = data.get("delegated_to_type") or "team_member"
        delegated_to_user, delegated_to_member = _resolve_assignment_target(
            delegated_to_id, preferred=preferred_delegate_type
        )
        if delegated_to_user is None and delegated_to_member is None:
            return jsonify({"msg": "Selected delegate was not found."}), 404

    delegations_payload = data.get("delegations") or []
    delegation_models: list[ResponsibilityDelegation] = []
    seen_delegates: set[tuple[str, int]] = set()
    for entry in delegations_payload:
        delegate_id = entry.get("delegate_id")
        if delegate_id is None:
            continue
        preferred_delegate_type = entry.get("delegate_type") or "team_member"
        delegate_user, delegate_member = _resolve_assignment_target(
            delegate_id, preferred=preferred_delegate_type
        )
        if delegate_user is None and delegate_member is None:
            return jsonify({"msg": "Selected delegate was not found."}), 404
        key: tuple[str, int]
        if delegate_user is not None:
            key = ("user", int(delegate_user.id))
        else:
            key = ("member", int(delegate_member.id))
        if key in seen_delegates:
            continue
        seen_delegates.add(key)
        delegation = ResponsibilityDelegation(
            delegate_id=delegate_user.id if delegate_user else None,
            delegate_member_id=delegate_member.id if delegate_member else None,
            allocated_value=entry.get("allocated_value"),
        )
        if delegate_user is not None:
            delegation.delegate = delegate_user
        if delegate_member is not None:
            delegation.delegate_member = delegate_member
        delegation_models.append(delegation)

    if not delegation_models and (delegated_to_user is not None or delegated_to_member is not None):
        fallback_delegation = ResponsibilityDelegation(
            delegate_id=delegated_to_user.id if delegated_to_user else None,
            delegate_member_id=delegated_to_member.id if delegated_to_member else None,
        )
        if delegated_to_user is not None:
            fallback_delegation.delegate = delegated_to_user
        if delegated_to_member is not None:
            fallback_delegation.delegate_member = delegated_to_member
        delegation_models.append(fallback_delegation)

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
        cc_email=data.get("cc_email"),
        progress=progress,
        assigner_id=assigner_id,
        assignee_id=assignee_user.id if assignee_user else None,
        assignee_member_id=assignee_member.id if assignee_member else None,
        perf_uom=performance_unit,
        perf_responsible_value=performance_responsible_value,
        perf_actual_value=performance_actual_value,
        perf_metric_value=performance_metric_value,
        perf_input_type=performance_input_type,
        delegated_to_id=delegated_to_user.id if delegated_to_user else None,
        delegated_to_member_id=delegated_to_member.id if delegated_to_member else None,
    )

    if assignee_member is not None:
        task.assignee_member = assignee_member
    if delegated_to_member is not None:
        task.delegated_to_member = delegated_to_member

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
    response.update(_decorate_task_names(task))
    response["email_notification"] = notification
    return jsonify(response), 201


@bp.put("/<int:task_id>")
@jwt_required()
def update_task(task_id: int):
    """Update an existing responsibility task."""

    role = _current_role()
    if role not in _ALLOWED_CREATOR_ROLES:
        return jsonify({"msg": "You are not authorized to update responsibility tasks."}), 403

    task = ResponsibilityTask.query.get(task_id)
    if not task:
        return jsonify({"msg": "Responsibility task was not found."}), 404

    payload = request.get_json(silent=True) or {}
    try:
        data = task_create_schema.load(payload)
    except ValidationError as error:
        return jsonify({"errors": error.normalized_messages()}), 422

    before_state = _task_change_state(task)

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

    assignee_user: User | None = None
    assignee_member: TeamMember | None = None
    assignee_id = data.get("assignee_id")
    if assignee_id is not None:
        assignee_user, assignee_member = _resolve_assignment_target(
            assignee_id, preferred=data.get("assignee_type")
        )
        if assignee_user is None and assignee_member is None:
            return jsonify({"msg": "Selected assignee was not found."}), 404

    delegated_to_user: User | None = None
    delegated_to_member: TeamMember | None = None
    delegated_to_id = data.get("delegated_to_id")
    if delegated_to_id is not None:
        preferred_delegate_type = data.get("delegated_to_type") or "team_member"
        delegated_to_user, delegated_to_member = _resolve_assignment_target(
            delegated_to_id, preferred=preferred_delegate_type
        )
        if delegated_to_user is None and delegated_to_member is None:
            return jsonify({"msg": "Selected delegate was not found."}), 404

    delegations_payload = data.get("delegations") or []
    delegation_models: list[ResponsibilityDelegation] = []
    seen_delegates: set[tuple[str, int]] = set()
    for entry in delegations_payload:
        delegate_id = entry.get("delegate_id")
        if delegate_id is None:
            continue
        preferred_delegate_type = entry.get("delegate_type") or "team_member"
        delegate_user, delegate_member = _resolve_assignment_target(
            delegate_id, preferred=preferred_delegate_type
        )
        if delegate_user is None and delegate_member is None:
            return jsonify({"msg": "Selected delegate was not found."}), 404
        key: tuple[str, int]
        if delegate_user is not None:
            key = ("user", int(delegate_user.id))
        else:
            key = ("member", int(delegate_member.id))
        if key in seen_delegates:
            continue
        seen_delegates.add(key)
        delegation = ResponsibilityDelegation(
            delegate_id=delegate_user.id if delegate_user else None,
            delegate_member_id=delegate_member.id if delegate_member else None,
            allocated_value=entry.get("allocated_value"),
        )
        if delegate_user is not None:
            delegation.delegate = delegate_user
        if delegate_member is not None:
            delegation.delegate_member = delegate_member
        delegation_models.append(delegation)

    if not delegation_models and (delegated_to_user is not None or delegated_to_member is not None):
        fallback_delegation = ResponsibilityDelegation(
            delegate_id=delegated_to_user.id if delegated_to_user else None,
            delegate_member_id=delegated_to_member.id if delegated_to_member else None,
        )
        if delegated_to_user is not None:
            fallback_delegation.delegate = delegated_to_user
        if delegated_to_member is not None:
            fallback_delegation.delegate_member = delegated_to_member
        delegation_models.append(fallback_delegation)

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
    task.cc_email = data.get("cc_email")
    task.progress = progress
    task.assignee_id = assignee_user.id if assignee_user else None
    task.assignee_member_id = assignee_member.id if assignee_member else None
    task.replace_delegations(delegation_models)
    task.delegated_to_id = delegated_to_user.id if delegated_to_user else None
    task.delegated_to_member_id = (
        delegated_to_member.id if delegated_to_member else None
    )
    task.assignee_member = assignee_member
    task.delegated_to_member = delegated_to_member
    task.update_custom_weekdays(data.get("custom_weekdays"))
    task.perf_uom = performance_unit
    task.perf_responsible_value = performance_responsible_value
    task.perf_actual_value = performance_actual_value
    task.perf_metric_value = performance_metric_value
    task.perf_input_type = performance_input_type

    after_state = _task_change_state(task)
    changes = _collect_task_changes(before_state, after_state)
    detail_changed = any(change.get("label") == "Detail" for change in changes)
    notes_changed = any(change.get("label") == "Notes" for change in changes)

    error_message = "Unable to update responsibility. Please try again."
    try:
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        current_app.logger.exception("Failed to update responsibility task.", exc_info=error)
        return jsonify({"msg": error_message}), 500

    notification: dict[str, object] = {"sent": False}
    if changes:
        updater = User.query.get(_current_user_id())
        updater_name = _normalize_user_name(updater) or "A manager"
        try:
            _send_task_update_email(
                task,
                changes=changes,
                detail_changed=detail_changed,
                notes_changed=notes_changed,
                updated_by=updater_name,
            )
            notification["sent"] = True
        except Exception as error:  # pragma: no cover - defensive logging
            current_app.logger.exception("Failed to send responsibility email")
            notification = {
                "sent": False,
                "message": str(error),
            }
    else:
        notification["message"] = "No changes detected; email not sent."

    response = task_schema.dump(task)
    response.update(_decorate_task_names(task))
    response["email_notification"] = notification
    return jsonify(response), 200


@bp.get("")
@jwt_required()
def list_tasks():
    """Return responsibility tasks ordered by start date and creation time."""

    role = _current_role()
    if role not in _ALLOWED_CREATOR_ROLES:
        return jsonify({"msg": "You are not authorized to view responsibility tasks."}), 403

    query = (
        ResponsibilityTask.query.options(
            selectinload(ResponsibilityTask.assigner),
            selectinload(ResponsibilityTask.assignee),
            selectinload(ResponsibilityTask.assignee_member),
            selectinload(ResponsibilityTask.delegated_to),
            selectinload(ResponsibilityTask.delegated_to_member),
            selectinload(ResponsibilityTask.delegations).selectinload(ResponsibilityDelegation.delegate),
            selectinload(ResponsibilityTask.delegations).selectinload(ResponsibilityDelegation.delegate_member),
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
        query = query.filter(
            or_(
                ResponsibilityTask.assignee_id == assignee_value,
                ResponsibilityTask.assignee_member_id == assignee_value,
            )
        )

    tasks = query.all()
    payload = tasks_schema.dump(tasks)
    for item, model in zip(payload, tasks):
        item.update(_decorate_task_names(model))
    return jsonify(payload)


@bp.post("/send-weekly")
@jwt_required()
def send_weekly_plan():
    """Send a weekly summary of responsibilities to a recipient."""

    role = _current_role()
    if role not in _ALLOWED_CREATOR_ROLES:
        return jsonify({"msg": "You are not authorized to send weekly plans."}), 403

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


@bp.get("/reports/member-summary")
@jwt_required()
def responsibility_member_summary():
    """Return a responsibility occurrence summary grouped by team member."""

    role = _current_role()
    if role not in _REPORT_ALLOWED_ROLES:
        return (
            jsonify({"msg": "You are not allowed to view responsibility reports."}),
            403,
        )

    today = date.today()
    default_end = today
    default_start = today - timedelta(days=29)

    start_value = request.args.get("startDate")
    end_value = request.args.get("endDate")

    try:
        start_date = date.fromisoformat(start_value) if start_value else default_start
    except ValueError:
        return jsonify({"msg": "startDate must be YYYY-MM-DD."}), 422

    try:
        end_date = date.fromisoformat(end_value) if end_value else default_end
    except ValueError:
        return jsonify({"msg": "endDate must be YYYY-MM-DD."}), 422

    if end_date < start_date:
        return jsonify({"msg": "endDate must be on or after startDate."}), 422

    raw_member_filters = request.args.getlist("teamMemberId")
    member_filter: set[int] | None = None
    if raw_member_filters:
        candidate_ids: set[int] = set()
        for raw_value in raw_member_filters:
            if isinstance(raw_value, str) and raw_value.strip().lower() == "all":
                candidate_ids = set()
                break
            try:
                candidate_ids.add(int(raw_value))
            except (TypeError, ValueError):
                return jsonify({"msg": "teamMemberId must be numeric."}), 422
        if candidate_ids:
            member_filter = candidate_ids

    selected_members: dict[int, TeamMember] = {}
    if member_filter:
        members = TeamMember.query.filter(TeamMember.id.in_(member_filter)).all()
        selected_members = {member.id: member for member in members}
        if len(selected_members) != len(member_filter):
            return jsonify({"msg": "Unknown team member filter."}), 422

    query = (
        ResponsibilityTask.query.options(
            selectinload(ResponsibilityTask.assigner),
            selectinload(ResponsibilityTask.assignee),
            selectinload(ResponsibilityTask.assignee_member),
            selectinload(ResponsibilityTask.delegated_to),
            selectinload(ResponsibilityTask.delegated_to_member),
            selectinload(ResponsibilityTask.delegations).selectinload(
                ResponsibilityDelegation.delegate
            ),
            selectinload(ResponsibilityTask.delegations).selectinload(
                ResponsibilityDelegation.delegate_member
            ),
        )
        .filter(ResponsibilityTask.scheduled_for <= end_date)
        .order_by(
            asc(ResponsibilityTask.scheduled_for),
            asc(ResponsibilityTask.created_at),
        )
    )

    tasks = query.all()

    task_members: dict[int, list[TeamMember]] = {}
    relevant_tasks: list[ResponsibilityTask] = []
    for task in tasks:
        members = _task_team_members(task)
        if not members:
            continue
        if member_filter is not None:
            member_ids = {
                getattr(member, "id", None)
                for member in members
                if getattr(member, "id", None) is not None
            }
            if not member_ids.intersection(member_filter):
                continue
        task_members[task.id] = members
        relevant_tasks.append(task)

    occurrences = _occurrences_for_range(relevant_tasks, start_date, end_date)
    serialized_tasks: dict[int, dict] = {}

    member_occurrences: dict[int, dict] = {}

    for occurrence_date, task in occurrences:
        members = task_members.get(task.id, [])
        if not members:
            continue

        if task.id not in serialized_tasks:
            serialized = task_schema.dump(task)
            serialized.update(_decorate_task_names(task))
            if "performanceUnit" in serialized:
                serialized["performanceUnitLabel"] = _performance_unit_label(
                    serialized.get("performanceUnit")
                )
            serialized_tasks[task.id] = serialized
        else:
            serialized = serialized_tasks[task.id]

        for member in members:
            member_id = getattr(member, "id", None)
            if member_id is None:
                continue
            try:
                member_id = int(member_id)
            except (TypeError, ValueError):
                continue
            if member_filter is not None and member_id not in member_filter:
                continue

            bucket = member_occurrences.setdefault(
                member_id,
                {
                    "member": member,
                    "occurrences": [],
                },
            )

            bucket["occurrences"].append(
                {
                    "date": occurrence_date.isoformat(),
                    "taskId": serialized.get("id"),
                    "taskNumber": serialized.get("number"),
                    "taskTitle": serialized.get("title"),
                    "taskStatus": serialized.get("status"),
                    "taskAction": serialized.get("action"),
                    "taskProgress": serialized.get("progress"),
                    "taskProgressLabel": _format_progress_label(
                        serialized.get("progress")
                    ),
                    "assigneeName": serialized.get("assigneeName"),
                    "delegatedToName": serialized.get("delegatedToName"),
                    "taskDescription": serialized.get("description"),
                    "taskDetail": serialized.get("detail"),
                    "taskDiscussion": serialized.get("actionNotes"),
                    "taskActionNotes": serialized.get("actionNotes"),
                    "taskPerformanceUnit": serialized.get("performanceUnit"),
                    "taskPerformanceUnitLabel": serialized.get("performanceUnitLabel")
                    or _performance_unit_label(serialized.get("performanceUnit")),
                    "taskPerformanceResponsible": serialized.get("performanceResponsible"),
                    "taskPerformanceActual": serialized.get("performanceActual"),
                    "taskPerformanceMetric": serialized.get("performanceMetric"),
                }
            )

    if member_filter:
        for member_id, member in selected_members.items():
            member_occurrences.setdefault(
                member_id,
                {
                    "member": member,
                    "occurrences": [],
                },
            )

    members_payload: list[dict] = []
    total_occurrences = 0

    for member_id, payload in member_occurrences.items():
        member = payload.get("member")
        occurrences_list = payload.get("occurrences", [])
        occurrences_list.sort(
            key=lambda item: (
                item.get("date") or "",
                item.get("taskNumber") or "",
                item.get("taskId") or 0,
            )
        )
        total_occurrences += len(occurrences_list)
        unique_task_ids = {
            item.get("taskId")
            for item in occurrences_list
            if item.get("taskId") is not None
        }
        members_payload.append(
            {
                "id": member_id,
                "name": _normalize_member_name(member) or getattr(member, "name", "Team member"),
                "occurrenceCount": len(occurrences_list),
                "uniqueTaskCount": len(unique_task_ids),
                "occurrences": occurrences_list,
            }
        )

    members_payload.sort(
        key=lambda item: (
            -item["occurrenceCount"],
            item["name"].lower() if isinstance(item["name"], str) else "",
        )
    )

    response = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "generatedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "totalOccurrences": total_occurrences,
        "memberCount": len(members_payload),
        "members": members_payload,
    }
    return jsonify(response), 200

