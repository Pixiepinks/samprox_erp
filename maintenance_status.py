from __future__ import annotations

from typing import Optional

from models import MaintenanceJobStatus

_STATUS_LABELS = {
    MaintenanceJobStatus.SUBMITTED: "Submitted",
    MaintenanceJobStatus.FORWARDED_TO_MAINTENANCE: "To Maintenance",
    MaintenanceJobStatus.RETURNED_TO_PRODUCTION: "To Production",
    MaintenanceJobStatus.NOT_YET_STARTED: "Not Yet Started",
    MaintenanceJobStatus.IN_PROGRESS: "In Progress",
    MaintenanceJobStatus.AWAITING_PARTS: "Awaiting Parts",
    MaintenanceJobStatus.ON_HOLD: "On Hold",
    MaintenanceJobStatus.TESTING: "Testing",
    MaintenanceJobStatus.COMPLETED_MAINTENANCE: "Completed (Maintenance)",
    MaintenanceJobStatus.COMPLETED_VERIFIED: "Completed (Production Verified)",
    MaintenanceJobStatus.REOPENED: "Reopened",
}

_STATUS_COLORS = {
    MaintenanceJobStatus.SUBMITTED: "blue",
    MaintenanceJobStatus.FORWARDED_TO_MAINTENANCE: "blue",
    MaintenanceJobStatus.IN_PROGRESS: "blue",
    MaintenanceJobStatus.TESTING: "blue",
    MaintenanceJobStatus.RETURNED_TO_PRODUCTION: "blue",
    MaintenanceJobStatus.NOT_YET_STARTED: "amber",
    MaintenanceJobStatus.AWAITING_PARTS: "amber",
    MaintenanceJobStatus.ON_HOLD: "amber",
    MaintenanceJobStatus.COMPLETED_MAINTENANCE: "green",
    MaintenanceJobStatus.COMPLETED_VERIFIED: "green",
    MaintenanceJobStatus.REOPENED: "red",
}


def _normalize_status(status: Optional[object]) -> Optional[MaintenanceJobStatus]:
    if status is None:
        return None
    if isinstance(status, MaintenanceJobStatus):
        return status
    try:
        return MaintenanceJobStatus(str(status))
    except ValueError:
        code = str(status).strip().upper()
        for enum_value in MaintenanceJobStatus:
            if enum_value.value == code:
                return enum_value
    return None


def get_status_label(status: Optional[object]) -> Optional[str]:
    normalized = _normalize_status(status)
    if normalized is None:
        return None
    return _STATUS_LABELS.get(normalized, normalized.value.replace("_", " ").title())


def get_status_color(status: Optional[object]) -> Optional[str]:
    normalized = _normalize_status(status)
    if normalized is None:
        return None
    return _STATUS_COLORS.get(normalized)


def get_status_badge_class(status: Optional[object]) -> str:
    color = get_status_color(status)
    return f"status-badge status-{color}" if color else "status-badge"


def get_status_code(status: Optional[object]) -> Optional[str]:
    normalized = _normalize_status(status)
    return normalized.value if normalized else None


__all__ = [
    "get_status_badge_class",
    "get_status_code",
    "get_status_color",
    "get_status_label",
]
