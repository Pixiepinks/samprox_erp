import calendar
from datetime import date, datetime
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import func, inspect, text
from sqlalchemy import types as sqltypes
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, ProgrammingError

from extensions import db
from models import (
    DailyProductionEntry,
    MachineAsset,
    PayCategory,
    RoleEnum,
    TeamAttendanceRecord,
    TeamLeaveBalance,
    TeamMember,
    TeamMemberStatus,
    TeamSalaryRecord,
    TeamWorkCalendarDay,
)  # keep import; not strictly required now
from routes.jobs import require_role
from schemas import (
    AttendanceRecordSchema,
    LeaveSummarySchema,
    SalaryRecordSchema,
    TeamMemberBankDetailSchema,
    TeamMemberSchema,
    WorkCalendarDaySchema,
)

bp = Blueprint("team", __name__, url_prefix="/api/team")

member_schema = TeamMemberSchema()
members_schema = TeamMemberSchema(many=True)
attendance_record_schema = AttendanceRecordSchema()
attendance_records_schema = AttendanceRecordSchema(many=True)
leave_summary_schema = LeaveSummarySchema()
salary_record_schema = SalaryRecordSchema()
salary_records_schema = SalaryRecordSchema(many=True)
work_calendar_day_schema = WorkCalendarDaySchema()
work_calendar_days_schema = WorkCalendarDaySchema(many=True)
bank_detail_schema = TeamMemberBankDetailSchema()

COLOMBO_ZONE = ZoneInfo("Asia/Colombo")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _ensure_schema():
    """Ensure the ``team_member`` table has the expected structure."""
    try:
        engine = db.engine
    except RuntimeError:
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "team_member" not in tables:
        TeamMember.__table__.create(bind=engine, checkfirst=True)
        inspector = inspect(engine)

    column_info = inspector.get_columns("team_member")
    columns = {c["name"] for c in column_info}
    column_types = {c["name"]: c.get("type") for c in column_info}
    statements: list[str] = []
    status_fixes: list[str] = []
    pay_category_fixes: list[str] = []

    if "image_url" not in columns:
        statements.append("ALTER TABLE team_member ADD COLUMN image_url VARCHAR(500)")

    dialect = engine.dialect.name

    if "pay_category" not in columns:
        if dialect == "sqlite":
            statements.append("ALTER TABLE team_member ADD COLUMN pay_category VARCHAR(50)")
        else:
            statements.append("ALTER TABLE team_member ADD COLUMN pay_category VARCHAR(50) DEFAULT 'Office'")
        if dialect != "sqlite":
            statements.append("ALTER TABLE team_member ALTER COLUMN pay_category SET NOT NULL")
            statements.append("ALTER TABLE team_member ALTER COLUMN pay_category DROP DEFAULT")

    if "status" in columns:
        status_type = column_types.get("status")
        if isinstance(status_type, (sqltypes.Enum, sqltypes.String)):
            status_expression = "status::text" if dialect == "postgresql" else "status"

            # Normalize legacy enum casing so SQLAlchemy can read the values safely.
            status_fixes.extend(
                [
                    f"UPDATE team_member SET status = 'Active' WHERE {status_expression} = 'ACTIVE'",
                    f"UPDATE team_member SET status = 'Inactive' WHERE {status_expression} = 'INACTIVE'",
                    f"UPDATE team_member SET status = 'On Leave' WHERE {status_expression} IN ('ON_LEAVE', 'ON LEAVE')",
                ]
            )

            trimmed_status = f"TRIM({status_expression})"
            lowered_trimmed_status = f"LOWER({trimmed_status})"
            normalized_status = f"REPLACE(REPLACE({lowered_trimmed_status}, '_', ' '), '-', ' ')"

            status_fixes.extend(
                [
                    f"UPDATE team_member SET status = 'Active' WHERE {lowered_trimmed_status} = 'active' AND status <> 'Active'",
                    f"UPDATE team_member SET status = 'Inactive' WHERE {lowered_trimmed_status} = 'inactive' AND status <> 'Inactive'",
                    f"UPDATE team_member SET status = 'On Leave' WHERE {normalized_status} = 'on leave' AND status <> 'On Leave'",
                    f"UPDATE team_member SET status = 'Active' WHERE status IS NULL OR {trimmed_status} = ''",
                ]
            )
        else:
            current_app.logger.warning(
                "Skipping team_member status normalization; unexpected column type %s", status_type
            )

    if "created_at" not in columns:
        statements.append("ALTER TABLE team_member ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        statements.append("UPDATE team_member SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
        if dialect != "sqlite":
            statements.append("ALTER TABLE team_member ALTER COLUMN created_at SET NOT NULL")
            statements.append("ALTER TABLE team_member ALTER COLUMN created_at DROP DEFAULT")

    if "updated_at" not in columns:
        statements.append("ALTER TABLE team_member ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        statements.append("UPDATE team_member SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
        if dialect != "sqlite":
            statements.append("ALTER TABLE team_member ALTER COLUMN updated_at SET NOT NULL")
            statements.append("ALTER TABLE team_member ALTER COLUMN updated_at DROP DEFAULT")

    optional_text_columns = {
        "personal_detail": "TEXT",
        "assignments": "TEXT",
        "training_records": "TEXT",
        "employment_log": "TEXT",
        "files": "TEXT",
        "assets": "TEXT",
    }
    for name, typ in optional_text_columns.items():
        if name not in columns:
            statements.append(f"ALTER TABLE team_member ADD COLUMN {name} {typ}")

    bank_detail_columns = {
        "bank_account_name": "VARCHAR(200)",
        "bank_name": "VARCHAR(200)",
        "branch_name": "VARCHAR(200)",
        "bank_account_number": "VARCHAR(120)",
    }
    for name, typ in bank_detail_columns.items():
        if name not in columns:
            statements.append(f"ALTER TABLE team_member ADD COLUMN {name} {typ}")

    if "pay_category" in columns:
        pay_category_expression = "pay_category::text" if dialect == "postgresql" else "pay_category"
        trimmed_pay_category = f"TRIM({pay_category_expression})"
        lowered_pay_category = f"LOWER({trimmed_pay_category})"
        normalized_pay_category = "CASE " \
            f"WHEN {lowered_pay_category} IN ('office', '') THEN 'Office' " \
            f"WHEN {lowered_pay_category} = 'factory' THEN 'Factory' " \
            f"WHEN {lowered_pay_category} = 'casual' THEN 'Casual' " \
            f"WHEN {lowered_pay_category} = 'other' THEN 'Other' " \
            f"ELSE 'Office' END"
        pay_category_fixes.extend(
            [
                f"UPDATE team_member SET pay_category = {normalized_pay_category} WHERE {trimmed_pay_category} IS NULL OR {trimmed_pay_category} = ''",
                f"UPDATE team_member SET pay_category = 'Office' WHERE {lowered_pay_category} NOT IN ('office', 'factory', 'casual', 'other')",
            ]
        )

    pay_category_expression = "pay_category::text" if dialect == "postgresql" else "pay_category"
    trimmed_pay_category = f"TRIM({pay_category_expression})"
    lowered_pay_category = f"LOWER({trimmed_pay_category})"
    normalized_pay_category = (
        "CASE "
        f"WHEN {lowered_pay_category} IN ('office', '') THEN 'Office' "
        f"WHEN {lowered_pay_category} = 'factory' THEN 'Factory' "
        f"WHEN {lowered_pay_category} = 'casual' THEN 'Casual' "
        f"WHEN {lowered_pay_category} = 'other' THEN 'Other' "
        "ELSE 'Office' END"
    )
    pay_category_fixes.extend(
        [
            f"UPDATE team_member SET pay_category = {normalized_pay_category} WHERE pay_category IS NULL",
            f"UPDATE team_member SET pay_category = {normalized_pay_category} WHERE {lowered_pay_category} NOT IN ('office', 'factory', 'casual', 'other')",
        ]
    )

    if statements or status_fixes or pay_category_fixes:
        with engine.begin() as conn:
            for stmt in statements:
                try:
                    conn.execute(text(stmt))
                except (ProgrammingError, OperationalError, DataError):
                    current_app.logger.debug("Schema statement likely already applied: %s", stmt)
            for stmt in status_fixes:
                try:
                    conn.execute(text(stmt))
                except (ProgrammingError, OperationalError, DataError):
                    current_app.logger.debug("Status normalization statement failed or already applied: %s", stmt)
            for stmt in pay_category_fixes:
                try:
                    conn.execute(text(stmt))
                except (ProgrammingError, OperationalError, DataError):
                    current_app.logger.debug("Pay category normalization skipped or already applied: %s", stmt)


_MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")
_DATE_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

_ATTENDANCE_DAY_STATUS_VALUES = frozenset(
    [
        "Work Day",
        "Not Work Day",
        "Annual Leave",
        "Medical Leave",
        "Special Company Holiday",
        "No Pay Leave",
    ]
)
_LEAVE_ELIGIBLE_PAY_CATEGORIES = frozenset({PayCategory.OFFICE, PayCategory.FACTORY})
_ANNUAL_LEAVE_ENTITLEMENT = 14
_MEDICAL_LEAVE_ENTITLEMENT = 7
_LEAVE_BALANCE_SEED_MONTH = "2025-09"
_LEAVE_BALANCE_SEEDS = (
    ("E008", 12, 7),
    ("E005", 14, 7),
    ("E007", 12, 7),
    ("E006", 10, 7),
    ("E011", 14, 7),
    ("E009", 8, 7),
    ("E010", 0, 7),
)


def _ensure_tracking_tables():
    """Ensure the attendance & salary tables exist."""

    try:
        engine = db.engine
    except RuntimeError:
        return

    for model in (TeamAttendanceRecord, TeamSalaryRecord, TeamLeaveBalance):
        model.__table__.create(bind=engine, checkfirst=True)

    _seed_initial_leave_balances()


def _seed_initial_leave_balances():
    """Populate opening leave balances once for known members."""

    if not _LEAVE_BALANCE_SEEDS or not _LEAVE_BALANCE_SEED_MONTH:
        return

    try:
        session = db.session
    except RuntimeError:  # pragma: no cover - defensive
        return

    inserted = False

    for reg_number, annual_balance, medical_balance in _LEAVE_BALANCE_SEEDS:
        member = TeamMember.query.filter_by(reg_number=reg_number).one_or_none()
        if member is None or member.id is None:
            continue

        existing = TeamLeaveBalance.query.filter_by(
            team_member_id=member.id,
            month=_LEAVE_BALANCE_SEED_MONTH,
        ).one_or_none()

        if existing is not None:
            continue

        record = TeamLeaveBalance(
            team_member=member,
            month=_LEAVE_BALANCE_SEED_MONTH,
            work_days=0,
            no_pay_days=0,
            annual_brought_forward=int(annual_balance),
            annual_taken=0,
            annual_balance=int(annual_balance),
            medical_brought_forward=int(medical_balance),
            medical_taken=0,
            medical_balance=int(medical_balance),
        )
        session.add(record)
        inserted = True

    if inserted:
        try:
            session.commit()
        except Exception as exc:  # pragma: no cover - defensive safety net
            session.rollback()
            current_app.logger.warning("Failed to seed initial leave balances: %s", exc)


def _ensure_work_calendar_table():
    """Ensure the work calendar overrides table exists."""

    try:
        engine = db.engine
    except RuntimeError:
        return

    TeamWorkCalendarDay.__table__.create(bind=engine, checkfirst=True)


def _normalize_month(value: str | None) -> str | None:
    text_value = _clean_string(value)
    if not text_value:
        return None

    match = _MONTH_PATTERN.match(text_value)
    if not match:
        return None

    year, month = match.groups()
    try:
        month_number = int(month)
    except ValueError:
        return None

    if month_number < 1 or month_number > 12:
        return None

    return f"{year}-{month_number:02d}"


def _get_month_bounds(month: str) -> tuple[date, date, int] | None:
    if not month or not _MONTH_PATTERN.match(month):
        return None

    try:
        year = int(month[:4])
        month_number = int(month[5:7])
    except (TypeError, ValueError):
        return None

    if month_number < 1 or month_number > 12:
        return None

    month_start = date(year, month_number, 1)
    days_in_month = calendar.monthrange(year, month_number)[1]
    month_end = date(year, month_number, days_in_month)
    return month_start, month_end, days_in_month


def _normalize_day_status(value: str | None) -> str | None:
    text_value = _clean_string(value)
    if not text_value:
        return None
    if text_value not in _ATTENDANCE_DAY_STATUS_VALUES:
        return None
    return text_value


def _normalize_attendance_entries(entries: dict | None, *, month: str) -> dict[str, dict[str, str]]:
    if not isinstance(entries, dict):
        return {}

    normalized: dict[str, dict[str, str]] = {}

    for raw_date, raw_entry in entries.items():
        date_key = _clean_string(raw_date)
        if not date_key or not _DATE_PATTERN.match(date_key):
            continue

        if month and not date_key.startswith(month):
            continue

        if not isinstance(raw_entry, dict):
            continue

        on_time = _clean_string(raw_entry.get("onTime"))
        off_time = _clean_string(raw_entry.get("offTime"))

        entry_payload: dict[str, str] = {}
        if on_time:
            entry_payload["onTime"] = on_time
        if off_time:
            entry_payload["offTime"] = off_time

        day_status = _normalize_day_status(raw_entry.get("dayStatus"))
        if day_status:
            entry_payload["dayStatus"] = day_status

        if entry_payload:
            normalized[date_key] = entry_payload

    return normalized


def _normalize_salary_components(components: dict | None) -> dict[str, str]:
    if not isinstance(components, dict):
        return {}

    normalized: dict[str, str] = {}

    for raw_key, raw_value in components.items():
        key = _clean_string(raw_key)
        if not key or key == "grossSalary":
            continue

        value = _clean_string(raw_value)
        if not value:
            continue

        if key in _NUMERIC_SALARY_COMPONENT_KEYS:
            try:
                numeric_value = Decimal(value.replace(",", ""))
            except (InvalidOperation, ValueError):  # pragma: no cover - defensive
                label = _SALARY_COMPONENT_LABELS.get(key, key)
                raise ValueError(f"{label} must be a numeric value.")
            normalized[key] = _format_decimal_amount(numeric_value)
        else:
            normalized[key] = value

    total_day_value = normalized.get("totalDaySalary")
    production_value = normalized.get("production")

    if total_day_value is not None:
        normalized["production"] = total_day_value
        normalized["totalDaySalary"] = total_day_value
    elif production_value is not None:
        normalized["totalDaySalary"] = production_value

    return normalized


_MINUTES_PER_DAY = 24 * 60
_MEAL_BREAK_START_MINUTES = 12 * 60 + 45
_MEAL_BREAK_END_MINUTES = 13 * 60 + 45


def _parse_time_to_minutes(value: str | None) -> int | None:
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    parts = text.split(":", 1)
    if len(parts) != 2:
        return None

    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except (TypeError, ValueError):
        return None

    if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
        return None

    return hours * 60 + minutes


def _build_time_adjustments(rules: list[dict[str, str]]) -> list[tuple[int, int, int]]:
    adjustments: list[tuple[int, int, int]] = []

    for rule in rules:
        start = _parse_time_to_minutes(rule.get("start"))
        end = _parse_time_to_minutes(rule.get("end"))
        target = _parse_time_to_minutes(rule.get("target"))

        if start is None or end is None or target is None:
            continue

        adjustments.append((min(start, end), max(start, end), target))

    return adjustments


_ON_TIME_ADJUSTMENTS = _build_time_adjustments(
    [
        {"start": "06:36", "end": "07:04", "target": "07:00"},
        {"start": "07:05", "end": "07:16", "target": "07:15"},
        {"start": "07:17", "end": "07:31", "target": "07:30"},
        {"start": "07:32", "end": "07:46", "target": "07:45"},
        {"start": "07:46", "end": "08:04", "target": "08:00"},
    ]
)

_OFF_TIME_ADJUSTMENTS = _build_time_adjustments(
    [
        {"start": "18:59", "end": "19:14", "target": "19:00"},
        {"start": "18:44", "end": "18:58", "target": "18:45"},
        {"start": "18:29", "end": "18:43", "target": "18:30"},
        {"start": "18:14", "end": "18:28", "target": "18:15"},
        {"start": "17:58", "end": "18:13", "target": "18:00"},
    ]
)


def _apply_time_adjustment(minutes: int | None, adjustments: list[tuple[int, int, int]]) -> int | None:
    if minutes is None:
        return None

    for start, end, target in adjustments:
        if start <= minutes <= end:
            return target

    return minutes


def _compute_duration_minutes(start_minutes: int | None, end_minutes: int | None) -> int | None:
    if start_minutes is None or end_minutes is None:
        return None

    difference = end_minutes - start_minutes
    if difference < 0:
        difference += _MINUTES_PER_DAY

    if difference < 0:
        return None

    return difference


def _compute_pay_minutes(on_value: str | None, off_value: str | None) -> int | None:
    on_minutes = _parse_time_to_minutes(on_value)
    off_minutes = _parse_time_to_minutes(off_value)

    if on_minutes is None or off_minutes is None:
        return None

    adjusted_on = _apply_time_adjustment(on_minutes, _ON_TIME_ADJUSTMENTS)
    adjusted_off = _apply_time_adjustment(off_minutes, _OFF_TIME_ADJUSTMENTS)

    if adjusted_on is None or adjusted_off is None:
        return None

    return _compute_duration_minutes(adjusted_on, adjusted_off)


def _does_period_include_meal_break(on_minutes: int | None, off_minutes: int | None) -> bool:
    if on_minutes is None or off_minutes is None:
        return False

    adjusted_end = off_minutes
    if adjusted_end <= on_minutes:
        adjusted_end += _MINUTES_PER_DAY

    intervals = [
        (_MEAL_BREAK_START_MINUTES, _MEAL_BREAK_END_MINUTES),
        (
            _MEAL_BREAK_START_MINUTES + _MINUTES_PER_DAY,
            _MEAL_BREAK_END_MINUTES + _MINUTES_PER_DAY,
        ),
    ]

    return any(on_minutes < end and adjusted_end > start for start, end in intervals)


def _build_work_calendar_lookup(month: str) -> dict[str, bool]:
    if not month or len(month) != 7 or month[4] != "-":
        return {}

    try:
        year = int(month[:4])
        month_number = int(month[5:])
    except ValueError:
        return {}

    if month_number < 1 or month_number > 12:
        return {}

    start_date = date(year, month_number, 1)
    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    records = (
        TeamWorkCalendarDay.query.filter(TeamWorkCalendarDay.date >= start_date)
        .filter(TeamWorkCalendarDay.date < next_month)
        .all()
    )

    return {record.date.isoformat(): record.is_work_day is not False for record in records}


def _compute_regular_and_overtime_minutes(
    iso: str,
    on_value: str | None,
    off_value: str | None,
    pay_minutes: int | None,
    work_calendar_lookup: dict[str, bool],
) -> tuple[int | None, int | None]:
    if not iso or pay_minutes is None or pay_minutes < 0:
        return None, None

    is_work_day = work_calendar_lookup.get(iso)
    if is_work_day is None:
        is_work_day = True

    if not is_work_day:
        on_minutes = _parse_time_to_minutes(on_value)
        off_minutes = _parse_time_to_minutes(off_value)
        overtime_minutes = pay_minutes

        if (
            overtime_minutes > 0
            and _does_period_include_meal_break(on_minutes, off_minutes)
        ):
            overtime_minutes = max(overtime_minutes - 60, 0)

        return 0, overtime_minutes

    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return pay_minutes, 0

    regular_limit = 9 * 60
    if day.weekday() == 5:
        regular_limit = 5 * 60

    overtime_minutes = max(pay_minutes - regular_limit, 0)
    regular_minutes = max(min(pay_minutes, regular_limit), 0)

    return regular_minutes, overtime_minutes


def _calculate_entry_overtime_minutes(
    iso: str, entry: dict, work_calendar_lookup: dict[str, bool]
) -> int:
    if not isinstance(entry, dict):
        return 0

    on_value = entry.get("onTime")
    off_value = entry.get("offTime")

    if not on_value and not off_value:
        return 0

    pay_minutes = _compute_pay_minutes(on_value, off_value)
    if pay_minutes is None:
        return 0

    _, overtime_minutes = _compute_regular_and_overtime_minutes(
        iso, on_value, off_value, pay_minutes, work_calendar_lookup
    )

    if overtime_minutes is None:
        return 0

    return int(overtime_minutes)


def _calculate_monthly_overtime_minutes(
    entries: dict | None, month: str, work_calendar_lookup: dict[str, bool]
) -> int:
    if not isinstance(entries, dict) or not month:
        return 0

    total = 0
    for iso, entry in entries.items():
        if not isinstance(iso, str) or not iso.startswith(month):
            continue

        total += _calculate_entry_overtime_minutes(iso, entry, work_calendar_lookup)

    return total


def _decimal_from_value(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.replace(",", "").strip()
        if not text:
            return Decimal("0")
        try:
            return Decimal(text)
        except InvalidOperation:
            return Decimal("0")
    return Decimal("0")


def _format_decimal_amount(value: Decimal | None) -> str:
    if value is None:
        value = Decimal("0")

    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(quantized, "f")


def _normalize_numeric_component_map(components: dict | None) -> dict[str, object]:
    normalized: dict[str, object] = {}

    if not isinstance(components, dict):
        return normalized

    for raw_key, raw_value in components.items():
        if not isinstance(raw_key, str):
            continue

        if raw_key in _NUMERIC_SALARY_COMPONENT_KEYS:
            normalized[raw_key] = _format_decimal_amount(_decimal_from_value(raw_value))
        else:
            normalized[raw_key] = raw_value

    return normalized


def _get_gross_component_keys(pay_category: PayCategory | None) -> tuple[str, ...]:
    if isinstance(pay_category, PayCategory):
        keys = _GROSS_COMPONENT_KEY_MAP.get(pay_category)
        if keys:
            return keys

    return _GROSS_COMPONENT_KEY_MAP[PayCategory.OFFICE]


def _compute_gross_salary_amount(
    member: TeamMember | None, components: dict | None
) -> Decimal:
    pay_category = _resolve_pay_category(member)
    component_keys = _get_gross_component_keys(pay_category)
    values = components if isinstance(components, dict) else {}

    total = Decimal("0.00")
    for key in component_keys:
        lookup_keys = _GROSS_COMPONENT_ALIAS_MAP.get(key)
        if lookup_keys:
            for alias in lookup_keys:
                if alias in values:
                    total += _decimal_from_value(values.get(alias))
                    break
            else:
                total += _decimal_from_value(values.get(key))
            continue

        total += _decimal_from_value(values.get(key))

    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _apply_computed_gross_component(
    member: TeamMember | None, components: dict | None
) -> tuple[dict[str, object], Decimal]:
    normalized = _normalize_numeric_component_map(components)
    gross_amount = _compute_gross_salary_amount(member, normalized)
    normalized["grossSalary"] = _format_decimal_amount(gross_amount)
    return normalized, gross_amount


def _resolve_pay_category(member: TeamMember | None) -> PayCategory | None:
    if member is None:
        return None

    pay_category = getattr(member, "pay_category", None)
    if isinstance(pay_category, PayCategory):
        return pay_category
    if isinstance(pay_category, str):
        try:
            return PayCategory(pay_category)
        except ValueError:
            return None
    return None


def _resolve_leave_entitlements(member: TeamMember | None) -> tuple[int, int]:
    pay_category = _resolve_pay_category(member)
    if pay_category not in _LEAVE_ELIGIBLE_PAY_CATEGORIES:
        return 0, 0
    return _ANNUAL_LEAVE_ENTITLEMENT, _MEDICAL_LEAVE_ENTITLEMENT


def _previous_month(month: str) -> str | None:
    if not month or len(month) != 7 or month[4] != "-":
        return None
    try:
        year = int(month[:4])
        month_number = int(month[5:])
    except ValueError:
        return None

    if month_number < 1 or month_number > 12:
        return None

    if month_number == 1:
        if year <= 1:
            return None
        return f"{year - 1}-12"

    return f"{year}-{month_number - 1:02d}"


def _calculate_leave_counts(entries: dict | None, month: str) -> dict[str, int]:
    counts = {
        "work_days": 0,
        "no_pay_days": 0,
        "annual_leave": 0,
        "medical_leave": 0,
    }

    if not isinstance(entries, dict) or not month:
        return counts

    for iso, entry in entries.items():
        if not isinstance(iso, str) or not iso.startswith(month):
            continue

        if not isinstance(entry, dict):
            continue

        on_value = entry.get("onTime")
        off_value = entry.get("offTime")
        pay_minutes = _compute_pay_minutes(on_value, off_value)
        if isinstance(pay_minutes, int) and pay_minutes > 0:
            counts["work_days"] += 1

        day_status = _normalize_day_status(entry.get("dayStatus"))
        if not day_status:
            continue

        if day_status == "No Pay Leave":
            counts["no_pay_days"] += 1
        elif day_status == "Annual Leave":
            counts["annual_leave"] += 1
        elif day_status == "Medical Leave":
            counts["medical_leave"] += 1

    return counts


def _build_leave_summary(
    member: TeamMember | None, month: str, entries: dict | None
) -> dict[str, dict | int]:
    counts = _calculate_leave_counts(entries, month)
    annual_entitlement, medical_entitlement = _resolve_leave_entitlements(member)

    annual_brought_forward = annual_entitlement
    medical_brought_forward = medical_entitlement

    if month and not month.endswith("-01"):
        previous_month = _previous_month(month)
        member_id = getattr(member, "id", None)
        if member_id is not None and previous_month:
            previous_record = TeamLeaveBalance.query.filter_by(
                team_member_id=member_id, month=previous_month
            ).one_or_none()
            if previous_record is not None:
                annual_brought_forward = int(previous_record.annual_balance or 0)
                medical_brought_forward = int(previous_record.medical_balance or 0)

    annual_balance = annual_brought_forward - counts["annual_leave"]
    medical_balance = medical_brought_forward - counts["medical_leave"]

    return {
        "workDays": int(counts["work_days"]),
        "noPayDays": int(counts["no_pay_days"]),
        "annual": {
            "broughtForward": int(annual_brought_forward),
            "thisMonth": int(counts["annual_leave"]),
            "balance": int(annual_balance),
        },
        "medical": {
            "broughtForward": int(medical_brought_forward),
            "thisMonth": int(counts["medical_leave"]),
            "balance": int(medical_balance),
        },
    }


def _summarize_leave_balance_record(record: TeamLeaveBalance | None) -> dict | None:
    if record is None:
        return None

    return {
        "workDays": int(record.work_days or 0),
        "noPayDays": int(record.no_pay_days or 0),
        "annual": {
            "broughtForward": int(record.annual_brought_forward or 0),
            "thisMonth": int(record.annual_taken or 0),
            "balance": int(record.annual_balance or 0),
        },
        "medical": {
            "broughtForward": int(record.medical_brought_forward or 0),
            "thisMonth": int(record.medical_taken or 0),
            "balance": int(record.medical_balance or 0),
        },
    }


def _update_leave_balance(
    member: TeamMember, month: str, entries: dict | None
) -> tuple[dict[str, dict | int], TeamLeaveBalance | None, bool]:
    summary = _build_leave_summary(member, month, entries)

    member_id = getattr(member, "id", None)
    if member_id is None or not month:
        return summary, None, False

    record = TeamLeaveBalance.query.filter_by(team_member_id=member_id, month=month).one_or_none()
    created = False
    if record is None:
        record = TeamLeaveBalance(team_member=member, month=month)
        created = True

    changed = created

    def _assign(attr: str, value: int) -> None:
        nonlocal changed
        current = getattr(record, attr)
        if current != value:
            setattr(record, attr, value)
            changed = True

    _assign("work_days", int(summary["workDays"]))
    _assign("no_pay_days", int(summary["noPayDays"]))
    _assign("annual_brought_forward", int(summary["annual"]["broughtForward"]))
    _assign("annual_taken", int(summary["annual"]["thisMonth"]))
    _assign("annual_balance", int(summary["annual"]["balance"]))
    _assign("medical_brought_forward", int(summary["medical"]["broughtForward"]))
    _assign("medical_taken", int(summary["medical"]["thisMonth"]))
    _assign("medical_balance", int(summary["medical"]["balance"]))

    if changed:
        record.updated_at = datetime.utcnow()
        db.session.add(record)

    return summary, record, changed


def _compute_overtime_amount_for_member(
    member: TeamMember | None,
    components: dict | None,
    entries: dict | None,
    month: str,
    work_calendar_lookup: dict[str, bool],
) -> Decimal:
    pay_category = _resolve_pay_category(member)
    if pay_category not in {PayCategory.FACTORY, PayCategory.CASUAL}:
        return Decimal("0")

    total_minutes = _calculate_monthly_overtime_minutes(entries, month, work_calendar_lookup)
    if total_minutes <= 0:
        return Decimal("0")

    total_hours = Decimal(total_minutes) / Decimal(60)

    if pay_category == PayCategory.CASUAL:
        casual_rate = _decimal_from_value((components or {}).get("casualOtRate"))
        if casual_rate <= 0:
            return Decimal("0")

        amount = casual_rate * total_hours
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    basic_salary = _decimal_from_value((components or {}).get("basicSalary"))
    if basic_salary <= 0:
        return Decimal("0")

    hourly_rate = (basic_salary / Decimal("200")) * Decimal("1.5")
    amount = hourly_rate * total_hours
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _compute_monthly_overtime_amounts(
    month: str, component_sources: dict[int, dict | None]
) -> dict[int, str]:
    if not component_sources:
        return {}

    member_ids = [member_id for member_id in component_sources.keys() if isinstance(member_id, int)]
    if not member_ids:
        return {}

    attendance_records = (
        TeamAttendanceRecord.query.filter(TeamAttendanceRecord.month == month)
        .filter(TeamAttendanceRecord.team_member_id.in_(member_ids))
        .all()
    )
    attendance_lookup = {
        record.team_member_id: record.entries if isinstance(record.entries, dict) else {}
        for record in attendance_records
    }

    members = TeamMember.query.filter(TeamMember.id.in_(member_ids)).all()
    member_lookup = {member.id: member for member in members}

    work_calendar_lookup = _build_work_calendar_lookup(month)

    result: dict[int, str] = {}
    for member_id in member_ids:
        member = member_lookup.get(member_id)
        components = component_sources.get(member_id) or {}
        entries = attendance_lookup.get(member_id, {})
        amount = _compute_overtime_amount_for_member(
            member, components, entries, month, work_calendar_lookup
        )
        result[member_id] = _format_decimal_amount(amount)

    return result


def _compute_no_pay_amounts(
    month: str,
    component_sources: dict[int, dict | None],
    member_lookup: dict[int, TeamMember] | None,
) -> dict[int, str]:
    if not month or not component_sources:
        return {}

    member_ids = [
        member_id for member_id in component_sources.keys() if isinstance(member_id, int)
    ]
    if not member_ids:
        return {}

    leave_records = (
        TeamLeaveBalance.query.filter(TeamLeaveBalance.month == month)
        .filter(TeamLeaveBalance.team_member_id.in_(member_ids))
        .all()
    )
    no_pay_days_lookup: dict[int, int] = {
        record.team_member_id: int(record.no_pay_days or 0)
        for record in leave_records
    }

    missing_ids = [
        member_id for member_id in member_ids if member_id not in no_pay_days_lookup
    ]
    if missing_ids:
        attendance_records = (
            TeamAttendanceRecord.query.filter(TeamAttendanceRecord.month == month)
            .filter(TeamAttendanceRecord.team_member_id.in_(missing_ids))
            .all()
        )
        for attendance_record in attendance_records:
            if isinstance(attendance_record.entries, dict):
                entries = attendance_record.entries
            else:
                entries = {}
            counts = _calculate_leave_counts(entries, month)
            no_pay_days_lookup[attendance_record.team_member_id] = int(
                counts["no_pay_days"]
            )
        for member_id in missing_ids:
            no_pay_days_lookup.setdefault(member_id, 0)

    resolved_member_lookup = dict(member_lookup or {})

    results: dict[int, str] = {}
    for member_id in member_ids:
        member = resolved_member_lookup.get(member_id)
        if member is None:
            member = TeamMember.query.get(member_id)
            if member is not None:
                resolved_member_lookup[member_id] = member

        pay_category = _resolve_pay_category(member)
        if pay_category != PayCategory.FACTORY:
            results[member_id] = _format_decimal_amount(Decimal("0"))
            continue

        components = component_sources.get(member_id) or {}
        if not isinstance(components, dict):
            components = {}

        basic_salary = _decimal_from_value(components.get("basicSalary"))
        if basic_salary <= 0:
            results[member_id] = _format_decimal_amount(Decimal("0"))
            continue

        no_pay_days = Decimal(no_pay_days_lookup.get(member_id, 0))
        if no_pay_days <= 0:
            results[member_id] = _format_decimal_amount(Decimal("0"))
            continue

        amount = (basic_salary / Decimal("240")) * Decimal("8") * no_pay_days
        results[member_id] = _format_decimal_amount(amount)

    return results


def _apply_computed_overtime_component(
    member: TeamMember | None, month: str, components: dict | None
) -> tuple[dict[str, str], Decimal]:
    normalized_components: dict[str, str] = {}
    if isinstance(components, dict):
        normalized_components = dict(components)

    if not member or member.id is None or not month:
        normalized_components["overtime"] = _format_decimal_amount(Decimal("0"))
        return normalized_components, Decimal("0")

    attendance_record = TeamAttendanceRecord.query.filter_by(
        team_member_id=member.id, month=month
    ).one_or_none()
    if attendance_record and isinstance(attendance_record.entries, dict):
        entries = attendance_record.entries
    else:
        entries = {}

    work_calendar_lookup = _build_work_calendar_lookup(month)
    amount = _compute_overtime_amount_for_member(
        member, normalized_components, entries, month, work_calendar_lookup
    )
    normalized_components["overtime"] = _format_decimal_amount(amount)

    return normalized_components, amount


def _resolve_target_allowance_base(total_tons: Decimal, *, is_special: bool) -> Decimal:
    if total_tons is None:
        return Decimal("0")

    for lower_bound, upper_bound, regular_value, special_value in _TARGET_ALLOWANCE_SLABS:
        if total_tons >= lower_bound and total_tons < upper_bound:
            return special_value if is_special else regular_value

    if _TARGET_ALLOWANCE_SLABS:
        _, last_upper, _, _ = _TARGET_ALLOWANCE_SLABS[-1]
    else:
        last_upper = Decimal("0")

    if total_tons >= last_upper:
        regular_top, special_top = _TARGET_ALLOWANCE_TOP_VALUES
        return special_top if is_special else regular_top

    return Decimal("0")


def _compute_target_allowance_amounts(month: str) -> dict[int, Decimal]:
    bounds = _get_month_bounds(month)
    if not bounds:
        return {}

    month_start, month_end, days_in_month = bounds
    today = date.today()
    is_current_month = today.year == month_start.year and today.month == month_start.month

    query_end = month_end
    if is_current_month:
        query_end = min(today, month_end)

    totals_query = (
        db.session.query(
            DailyProductionEntry.date,
            func.coalesce(func.sum(DailyProductionEntry.quantity_tons), 0.0),
        )
        .join(MachineAsset, DailyProductionEntry.asset_id == MachineAsset.id)
        .filter(
            DailyProductionEntry.date >= month_start,
            DailyProductionEntry.date <= query_end,
            MachineAsset.code.in_(_TARGET_ALLOWANCE_MACHINE_CODES),
        )
        .group_by(DailyProductionEntry.date)
        .all()
    )

    total_tons = Decimal("0")
    production_days = 0

    for date_value, total_value in totals_query:
        if not isinstance(date_value, date):
            continue
        day_total = _decimal_from_value(total_value)
        total_tons += day_total
        if day_total > Decimal("0"):
            production_days += 1

    remaining_days = 0
    if is_current_month:
        remaining_days = max(days_in_month - min(today.day, days_in_month), 0)

    effective_production_days = production_days
    if remaining_days and days_in_month > 0:
        effective_production_days = min(production_days + remaining_days, days_in_month)

    attendance_records = TeamAttendanceRecord.query.filter_by(month=month).all()
    attendance_lookup = {
        record.team_member_id: record.entries if isinstance(record.entries, dict) else {}
        for record in attendance_records
    }
    leave_balances = TeamLeaveBalance.query.filter_by(month=month).all()
    leave_workdays_lookup = {
        balance.team_member_id: int(balance.work_days or 0) for balance in leave_balances
    }

    members = (
        TeamMember.query.filter(TeamMember.status == TeamMemberStatus.ACTIVE)
        .order_by(TeamMember.id.asc())
        .all()
    )

    allowances: dict[int, Decimal] = {}

    for member in members:
        pay_category = _resolve_pay_category(member)
        if pay_category != PayCategory.FACTORY:
            continue

        reg_number = _clean_string(getattr(member, "reg_number", ""))
        is_special = reg_number.upper() == _TARGET_ALLOWANCE_SPECIAL_REG_NO

        base_amount = _resolve_target_allowance_base(total_tons, is_special=is_special)
        if base_amount <= 0:
            allowances[member.id] = Decimal("0")
            continue

        leave_work_days = leave_workdays_lookup.get(member.id)
        if leave_work_days is not None:
            actual_attendance_days = min(max(leave_work_days, 0), days_in_month)
        else:
            entries = attendance_lookup.get(member.id)
            actual_attendance_days = _count_attendance_days(entries, month=month)

        effective_attendance_days = actual_attendance_days
        adjusted_production_days = effective_production_days

        if is_current_month and days_in_month > 0:
            effective_attendance_days = min(
                actual_attendance_days + remaining_days,
                days_in_month,
            )
            adjusted_production_days = max(
                0,
                min(production_days + remaining_days, days_in_month),
            )

        if effective_attendance_days < _TARGET_ALLOWANCE_MIN_ATTENDANCE_DAYS:
            allowances[member.id] = Decimal("0")
            continue

        if adjusted_production_days <= 0:
            allowances[member.id] = Decimal("0")
            continue

        capped_attendance_days = min(effective_attendance_days, adjusted_production_days)
        ratio = Decimal(capped_attendance_days) / Decimal(adjusted_production_days)
        allowance_value = (base_amount * ratio).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        allowances[member.id] = allowance_value

    return allowances


def _clean_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _count_attendance_days(entries: dict | None, *, month: str) -> int:
    if not isinstance(entries, dict) or not month:
        return 0

    total = 0
    for iso, entry in entries.items():
        if not isinstance(iso, str) or not iso.startswith(month):
            continue
        if not isinstance(entry, dict):
            continue

        on_value = _clean_string(entry.get("onTime"))
        off_value = _clean_string(entry.get("offTime"))

        if on_value or off_value:
            total += 1

    return total


def _current_colombo_year_month() -> tuple[int, int]:
    """Return the current year and month in the Asia/Colombo timezone."""

    now = datetime.now(COLOMBO_ZONE)
    return now.year, now.month


def _normalize_year_month_params(year_value, month_value) -> tuple[int, int]:
    """Parse year/month query parameters with Colombo defaults."""

    default_year, default_month = _current_colombo_year_month()

    try:
        year = int(year_value)
    except (TypeError, ValueError):
        year = default_year
    else:
        if year < 1 or year > 9999:
            year = default_year

    try:
        month = int(month_value)
    except (TypeError, ValueError):
        month = default_month
    else:
        if month < 1 or month > 12:
            month = default_month

    return year, month


def _parse_iso_date(value: str) -> date:
    """Parse YYYY-MM-DD strings and raise ValueError if invalid."""

    text_value = _clean_string(value)
    if not text_value:
        raise ValueError("Provide a date in YYYY-MM-DD format.")

    try:
        parsed = date.fromisoformat(text_value)
    except ValueError as exc:
        raise ValueError("Provide a date in YYYY-MM-DD format.") from exc

    return parsed


def _normalize_bool(value, *, label: str) -> bool:
    """Interpret booleans from payloads."""

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False

    raise ValueError(f"{label} must be true or false.")


def _sanitize_holiday_name(value) -> str | None:
    text_value = _clean_string(value)
    if not text_value:
        return None
    if len(text_value) > 120:
        text_value = text_value[:120].rstrip()
    return text_value or None


def _extract_string(value, *, label: str, required: bool = False, max_length: int | None = None) -> str | None:
    text_value = _clean_string(value)
    if not text_value:
        if required:
            raise ValueError(f"{label} is required.")
        return None
    if max_length is not None and len(text_value) > max_length:
        raise ValueError(f"{label} must be at most {max_length} characters.")
    return text_value


# ---- Date & status parsing -------------------------------------------------

_STATUS_MAP = {
    "active": TeamMemberStatus.ACTIVE,
    "inactive": TeamMemberStatus.INACTIVE,
    "on leave": TeamMemberStatus.ON_LEAVE,
    "on_leave": TeamMemberStatus.ON_LEAVE,
    "on-leave": TeamMemberStatus.ON_LEAVE,
}

_PAY_CATEGORY_MAP = {
    "office": PayCategory.OFFICE,
    "factory": PayCategory.FACTORY,
    "casual": PayCategory.CASUAL,
    "other": PayCategory.OTHER,
}


def _normalize_status(value) -> TeamMemberStatus:
    """Return the TeamMemberStatus enum that matches the provided value."""

    if isinstance(value, TeamMemberStatus):
        return value

    if value is None:
        return TeamMemberStatus.ACTIVE

    text_value = str(value).strip()
    if not text_value:
        return TeamMemberStatus.ACTIVE

    normalized = _STATUS_MAP.get(text_value.lower())
    if normalized is not None:
        return normalized

    try:
        # Allow exact enum values such as "Active"
        return TeamMemberStatus(text_value)
    except ValueError:
        canonical = re.sub(r"[\s_-]+", " ", text_value).strip().title()
        try:
            return TeamMemberStatus(canonical)
        except ValueError:
            return TeamMemberStatus.ACTIVE


def _normalize_pay_category(value) -> PayCategory:
    if isinstance(value, PayCategory):
        return value

    if value is None:
        return PayCategory.OFFICE

    text_value = str(value).strip()
    if not text_value:
        return PayCategory.OFFICE

    normalized = _PAY_CATEGORY_MAP.get(text_value.lower())
    if normalized:
        return normalized

    canonical = re.sub(r"[\s_-]+", " ", text_value).strip().title()
    return _PAY_CATEGORY_MAP.get(canonical.lower(), PayCategory.OFFICE)


def _parse_join_date(value, *, required: bool) -> date | None:
    """Parse a variety of human-friendly date formats used by the UI."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    s = _clean_string(value)
    if not s:
        if required:
            raise ValueError("Date of join is required.")
        return None

    normalized = re.sub(r"(?<=\b[A-Za-z]{3})\.", "", s)
    normalized = re.sub(r"(?<=\d)(st|nd|rd|th)", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(the|of)\b", " ", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace(",", " ")
    normalized = normalized.replace("\\", "/")
    normalized = normalized.strip().rstrip(".")

    candidates: set[str] = set()

    def _add_candidate(text: str) -> None:
        text = _clean_string(text)
        if text:
            candidates.add(text)

    _add_candidate(s)
    _add_candidate(s.rstrip("."))
    _add_candidate(normalized)
    _add_candidate(normalized.replace(".", "/"))
    _add_candidate(normalized.replace(".", "-"))

    compact = re.sub(r"\s*(/|-)\s*", r"\\1", normalized)
    _add_candidate(compact)
    _add_candidate(compact.replace("/", "-"))

    spaced = re.sub(r"[/\\-]", " ", normalized)
    _add_candidate(spaced)
    _add_candidate(spaced.title())

    normalized = re.sub(r"\s+", " ", normalized).strip()
    iso_like = normalized.replace("/", "-")
    _add_candidate(iso_like)

    patterns = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y %m %d",
        "%Y-%b-%d",
        "%Y-%B-%d",
        "%Y %b %d",
        "%Y %B %d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %m %Y",
        "%d.%m.%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m %d %Y",
    ]

    for cand in candidates:
        for fmt in patterns:
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue

    for cand in candidates:
        cleaned = cand
        if cleaned.endswith("Z"):
            cleaned = f"{cleaned[:-1]}+00:00"
        try:
            return datetime.fromisoformat(cleaned).date()
        except ValueError:
            continue

    raise ValueError("Invalid date for joinDate. Please use the YYYY-MM-DD format.")


def _data_error_message(exc: DataError, *, fallback: str) -> str:
    detail = ""
    origin = getattr(exc, "orig", None)
    if origin is not None:
        detail = str(origin)
    elif exc.args:
        detail = " ".join(str(a) for a in exc.args if a)
    lowered = detail.lower()
    if any(k in lowered for k in ("invalid", "incorrect", "out of range")) and "date" in lowered:
        return "Invalid date for joinDate. Please use the YYYY-MM-DD format."
    if "isoformat" in lowered and "date" in lowered:
        return "Invalid date for joinDate. Please use the YYYY-MM-DD format."
    return fallback


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.get("/members")
@jwt_required()
def list_members():
    try:
        _ensure_schema()
        members = TeamMember.query.order_by(TeamMember.reg_number.asc()).all()
        return jsonify(members_schema.dump(members))
    except (ProgrammingError, OperationalError):
        db.session.rollback()
        current_app.logger.exception("Failed to list team members due to schema issues.")
        _ensure_schema()
        members = TeamMember.query.order_by(TeamMember.reg_number.asc()).all()
        return jsonify(members_schema.dump(members))


@bp.post("/members")
@jwt_required()
def create_member():
    if not require_role(RoleEnum.admin, RoleEnum.production_manager):
        return jsonify({"msg": "Only administrators or production managers can register team members."}), 403

    _ensure_schema()
    payload = request.get_json() or {}

    # --- Extract text fields ---
    try:
        reg_number = _extract_string(payload.get("regNumber"), label="Registration number", required=True, max_length=40)
        name = _extract_string(payload.get("name"), label="Name", required=True, max_length=200)
        nickname = _extract_string(payload.get("nickname"), label="Nickname", max_length=120)
        epf = _extract_string(payload.get("epf"), label="EPF number", max_length=120)
        position = _extract_string(payload.get("position"), label="Position", max_length=120)
        image_url = _extract_string(payload.get("image"), label="Profile image URL", max_length=500)
        personal_detail = _extract_string(payload.get("personalDetail"), label="Personal detail")
        assignments = _extract_string(payload.get("assignments"), label="Assignments")
        training_records = _extract_string(payload.get("trainingRecords"), label="Training records")
        employment_log = _extract_string(payload.get("employmentLog"), label="Employment log")
        files_value = _extract_string(payload.get("files"), label="Files")
        assets_value = _extract_string(payload.get("assets"), label="Controlled assets")
        bank_account_name = _extract_string(
            payload.get("bankAccountName"), label="Bank account name", max_length=200
        )
        bank_name_value = _extract_string(payload.get("bankName"), label="Bank name", max_length=200)
        branch_name_value = _extract_string(
            payload.get("branchName"), label="Branch name", max_length=200
        )
        bank_account_number = _extract_string(
            payload.get("bankAccountNumber"), label="Bank account number", max_length=120
        )
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    # --- Parse date & status ---
    try:
        join_date = _parse_join_date(payload.get("joinDate"), required=False)
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400
    if join_date is None:
        join_date = date.today()

    # ✅ IMPORTANT: normalize to the TeamMemberStatus enum so the DB receives a valid value
    status_value = _normalize_status(payload.get("status"))
    pay_category_value = _normalize_pay_category(payload.get("payCategory"))

    # --- Create model ---
    member = TeamMember(
        reg_number=reg_number,
        name=name,
        nickname=nickname,
        epf=epf,
        position=position,
        pay_category=pay_category_value,
        join_date=join_date,
        status=status_value,
        image_url=image_url,
        personal_detail=personal_detail,
        assignments=assignments,
        training_records=training_records,
        employment_log=employment_log,
        files=files_value,
        assets=assets_value,
        bank_account_name=bank_account_name,
        bank_name=bank_name_value,
        branch_name=branch_name_value,
        bank_account_number=bank_account_number,
    )

    db.session.add(member)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"msg": f"Registration number {reg_number} already exists."}), 409
    except DataError as exc:
        db.session.rollback()
        message = _data_error_message(exc, fallback="Unable to register member. One or more fields exceed the allowed length.")
        current_app.logger.warning("Failed to create team member due to data error: %s", exc)
        return jsonify({"msg": message}), 400
    except (ProgrammingError, OperationalError):
        db.session.rollback()
        current_app.logger.exception("Failed to create team member due to schema issues.")
        _ensure_schema()
        db.session.add(member)
        db.session.commit()

    return jsonify(member_schema.dump(member)), 201


@bp.patch("/members/<int:member_id>")
@jwt_required()
def update_member(member_id: int):
    if not require_role(RoleEnum.admin):
        return jsonify({"msg": "Only administrators can update team members."}), 403

    _ensure_schema()
    member = TeamMember.query.get_or_404(member_id)
    payload = request.get_json() or {}

    if "name" in payload:
        try:
            member.name = _extract_string(payload.get("name"), label="Name", required=True, max_length=200)
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    field_definitions = [
        ("nickname", "Nickname", 120, "nickname"),
        ("epf", "EPF number", 120, "epf"),
        ("position", "Position", 120, "position"),
        ("image", "Profile image URL", 500, "image_url"),
        ("personalDetail", "Personal detail", None, "personal_detail"),
        ("assignments", "Assignments", None, "assignments"),
        ("trainingRecords", "Training records", None, "training_records"),
        ("employmentLog", "Employment log", None, "employment_log"),
        ("files", "Files", None, "files"),
        ("assets", "Controlled assets", None, "assets"),
        ("bankAccountName", "Bank account name", 200, "bank_account_name"),
        ("bankName", "Bank name", 200, "bank_name"),
        ("branchName", "Branch name", 200, "branch_name"),
        ("bankAccountNumber", "Bank account number", 120, "bank_account_number"),
    ]

    for key, label, maxlen, attr in field_definitions:
        if key in payload:
            try:
                value = _extract_string(payload.get(key), label=label, max_length=maxlen)
            except ValueError as exc:
                return jsonify({"msg": str(exc)}), 400
            setattr(member, attr, value)

    if "payCategory" in payload:
        member.pay_category = _normalize_pay_category(payload.get("payCategory"))

    if "joinDate" in payload:
        try:
            member.join_date = _parse_join_date(payload.get("joinDate"), required=True)
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "status" in payload:
        # ✅ normalize to the enum value directly
        member.status = _normalize_status(payload.get("status"))

    try:
        db.session.commit()
    except DataError as exc:
        db.session.rollback()
        message = _data_error_message(exc, fallback="Unable to update member. One or more fields exceed the allowed length.")
        current_app.logger.warning("Failed to update team member due to data error: %s", exc)
        return jsonify({"msg": message}), 400
    except (ProgrammingError, OperationalError):
        db.session.rollback()
        current_app.logger.exception("Failed to update team member due to schema issues.")
        _ensure_schema()
        db.session.add(member)
        db.session.commit()

    return jsonify(member_schema.dump(member))


@bp.get("/members/<int:member_id>/personal-detail")
@jwt_required()
def get_member_personal_detail(member_id: int):
    if not require_role(RoleEnum.admin, RoleEnum.production_manager):
        return (
            jsonify({"msg": "Only administrators or production managers can view personal details."}),
            403,
        )

    _ensure_schema()
    member = TeamMember.query.get_or_404(member_id)
    return jsonify(bank_detail_schema.dump(member))


@bp.patch("/members/<int:member_id>/personal-detail")
@jwt_required()
def update_member_personal_detail(member_id: int):
    if not require_role(RoleEnum.admin, RoleEnum.production_manager):
        return (
            jsonify({"msg": "Only administrators or production managers can update personal details."}),
            403,
        )

    _ensure_schema()
    member = TeamMember.query.get_or_404(member_id)
    payload = request.get_json() or {}

    field_definitions = [
        ("bankAccountName", "Bank account name", 200, "bank_account_name"),
        ("bankName", "Bank name", 200, "bank_name"),
        ("branchName", "Branch name", 200, "branch_name"),
        ("bankAccountNumber", "Bank account number", 120, "bank_account_number"),
    ]

    updates: dict[str, str | None] = {}

    for key, label, maxlen, attr in field_definitions:
        if key in payload:
            try:
                updates[attr] = _extract_string(payload.get(key), label=label, max_length=maxlen)
            except ValueError as exc:
                return jsonify({"msg": str(exc)}), 400

    if not updates:
        return jsonify(bank_detail_schema.dump(member))

    for attr, value in updates.items():
        setattr(member, attr, value)

    try:
        db.session.commit()
    except DataError as exc:
        db.session.rollback()
        message = _data_error_message(
            exc,
            fallback="Unable to update personal detail. One or more fields exceed the allowed length.",
        )
        current_app.logger.warning("Failed to update personal detail due to data error: %s", exc)
        return jsonify({"msg": message}), 400
    except (ProgrammingError, OperationalError):
        db.session.rollback()
        current_app.logger.exception("Failed to update personal detail due to schema issues.")
        _ensure_schema()
        db.session.add(member)
        db.session.commit()

    return jsonify(bank_detail_schema.dump(member))


@bp.get("/work-calendar")
@jwt_required()
def list_work_calendar():
    if not require_role(
        RoleEnum.admin,
        RoleEnum.production_manager,
        RoleEnum.maintenance_manager,
    ):
        return (
            jsonify({"msg": "Only administrators or managers can view the work calendar."}),
            403,
        )

    _ensure_schema()
    _ensure_work_calendar_table()

    year, month = _normalize_year_month_params(
        request.args.get("year"),
        request.args.get("month"),
    )

    start_day = date(year, month, 1)
    if month == 12:
        end_day = date(year + 1, 1, 1)
    else:
        end_day = date(year, month + 1, 1)

    records = (
        TeamWorkCalendarDay.query.filter(
            TeamWorkCalendarDay.date >= start_day,
            TeamWorkCalendarDay.date < end_day,
        )
        .order_by(TeamWorkCalendarDay.date.asc())
        .all()
    )

    return jsonify(
        {
            "year": year,
            "month": month,
            "days": work_calendar_days_schema.dump(records),
        }
    )


@bp.put("/work-calendar/<string:date_iso>")
@jwt_required()
def upsert_work_calendar_day(date_iso: str):
    if not require_role(RoleEnum.admin, RoleEnum.production_manager):
        return (
            jsonify({"msg": "Only administrators or production managers can update the work calendar."}),
            403,
        )

    try:
        target_date = _parse_iso_date(date_iso)
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    payload = request.get_json(silent=True) or {}
    if "isWorkDay" not in payload:
        return jsonify({"msg": "Work status is required."}), 400

    try:
        is_work_day = _normalize_bool(payload.get("isWorkDay"), label="Work status")
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    holiday_name = None
    if not is_work_day:
        holiday_name = _sanitize_holiday_name(payload.get("holidayName"))

    _ensure_schema()
    _ensure_work_calendar_table()

    record = TeamWorkCalendarDay.query.filter_by(date=target_date).one_or_none()

    if is_work_day and holiday_name is None:
        if record is not None:
            try:
                db.session.delete(record)
                db.session.commit()
            except Exception as exc:  # pragma: no cover - defensive
                db.session.rollback()
                current_app.logger.exception("Failed to clear work calendar day", exc_info=exc)
                return jsonify({"msg": "Unable to reset the selected day."}), 400
        return jsonify(
            {
                "date": target_date.isoformat(),
                "isWorkDay": True,
                "holidayName": None,
                "updatedAt": None,
            }
        )

    if record is None:
        record = TeamWorkCalendarDay(date=target_date)

    record.is_work_day = is_work_day
    record.holiday_name = holiday_name
    record.updated_at = datetime.utcnow()

    db.session.add(record)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Failed to save work calendar day", exc_info=exc)
        return jsonify({"msg": "Unable to save the selected day."}), 400

    return jsonify(work_calendar_day_schema.dump(record))


@bp.get("/attendance")
@jwt_required()
def list_attendance_records():
    month = _normalize_month(request.args.get("month"))
    if not month:
        return jsonify({"msg": "A valid month (YYYY-MM) is required."}), 400

    _ensure_schema()
    _ensure_tracking_tables()

    records = (
        TeamAttendanceRecord.query.filter_by(month=month)
        .order_by(TeamAttendanceRecord.team_member_id.asc())
        .all()
    )

    member_ids = [
        record.team_member_id for record in records if isinstance(record.team_member_id, int)
    ]
    leave_lookup: dict[int, TeamLeaveBalance] = {}
    if member_ids:
        leave_records = (
            TeamLeaveBalance.query.filter(TeamLeaveBalance.month == month)
            .filter(TeamLeaveBalance.team_member_id.in_(member_ids))
            .all()
        )
        leave_lookup = {leave.team_member_id: leave for leave in leave_records}

    for record in records:
        summary = _summarize_leave_balance_record(
            leave_lookup.get(record.team_member_id)
        )
        if summary is None:
            entries = record.entries if isinstance(record.entries, dict) else {}
            member = getattr(record, "team_member", None)
            summary = _build_leave_summary(member, month, entries)
        record.leave_summary = summary

    return jsonify({"month": month, "records": attendance_records_schema.dump(records)})


@bp.get("/attendance/<int:member_id>/summary")
@jwt_required()
def get_attendance_summary(member_id: int):
    month = _normalize_month(request.args.get("month"))
    if not month:
        return jsonify({"msg": "A valid month (YYYY-MM) is required."}), 400

    _ensure_schema()
    _ensure_tracking_tables()

    member = TeamMember.query.get_or_404(member_id)
    attendance_record = TeamAttendanceRecord.query.filter_by(
        team_member_id=member.id, month=month
    ).one_or_none()
    record_entries = getattr(attendance_record, "entries", None)
    entries = record_entries if isinstance(record_entries, dict) else {}

    summary, balance_record, changed = _update_leave_balance(member, month, entries)

    if changed:
        try:
            db.session.commit()
        except Exception as exc:  # pragma: no cover - defensive safety net
            db.session.rollback()
            current_app.logger.warning(
                "Failed to refresh leave balance while loading summary: %s", exc
            )
            balance_record = TeamLeaveBalance.query.filter_by(
                team_member_id=member.id, month=month
            ).one_or_none()
        else:
            balance_record = balance_record or TeamLeaveBalance.query.filter_by(
                team_member_id=member.id, month=month
            ).one_or_none()

    if balance_record is not None:
        summary = _summarize_leave_balance_record(balance_record) or summary

    if summary is None:
        summary = _build_leave_summary(member, month, entries)

    return jsonify({"memberId": member.id, "month": month, "leaveSummary": summary})


@bp.put("/attendance/<int:member_id>")
@jwt_required()
def upsert_attendance_record(member_id: int):
    if not require_role(RoleEnum.admin, RoleEnum.production_manager):
        return jsonify({"msg": "Only administrators or production managers can update attendance."}), 403

    _ensure_schema()
    _ensure_tracking_tables()

    member = TeamMember.query.get_or_404(member_id)
    payload = request.get_json() or {}
    month = _normalize_month(payload.get("month"))
    if not month:
        return jsonify({"msg": "A valid month (YYYY-MM) is required."}), 400

    entries_payload = payload.get("entries")
    normalized_entries = _normalize_attendance_entries(entries_payload, month=month)

    record = TeamAttendanceRecord.query.filter_by(team_member_id=member.id, month=month).one_or_none()

    summary: dict | None = None
    balance_record: TeamLeaveBalance | None = None
    leave_changed = False
    commit_required = False

    if normalized_entries:
        if record is None:
            record = TeamAttendanceRecord(team_member=member, month=month, entries=normalized_entries)
            db.session.add(record)
        else:
            record.entries = normalized_entries
        commit_required = True
        summary, balance_record, leave_changed = _update_leave_balance(
            member, month, normalized_entries
        )
    else:
        summary, balance_record, leave_changed = _update_leave_balance(member, month, {})
        if record is not None:
            db.session.delete(record)
            commit_required = True

    if leave_changed:
        commit_required = True

    try:
        if commit_required:
            db.session.commit()
    except (DataError, IntegrityError, ProgrammingError, OperationalError) as exc:
        db.session.rollback()
        current_app.logger.warning("Failed to save attendance record: %s", exc)
        return jsonify({"msg": "Unable to save attendance for the selected month."}), 400

    if balance_record is not None:
        summary = _summarize_leave_balance_record(balance_record) or summary
    if summary is None:
        summary = _build_leave_summary(
            member,
            month,
            normalized_entries if normalized_entries else {},
        )

    if normalized_entries:
        payload = attendance_record_schema.dump(record)
        if summary:
            payload["leaveSummary"] = summary
        return jsonify(payload)

    response = {"status": "deleted", "memberId": member.id, "month": month}
    if summary:
        response["leaveSummary"] = summary
    return jsonify(response)


_SALARY_COMPONENT_LABELS = {
    "basicSalary": "Basic salary",
    "daySalary": "Day salary",
    "generalAllowance": "General allowance",
    "transportAllowance": "Transport allowance",
    "attendanceAllowance": "Attendance allowance",
    "specialAllowance": "Special allowance",
    "performanceBonus": "Performance bonus",
    "production": "Total Day Salary",
    "totalDaySalary": "Total Day Salary",
    "targetAllowance": "Target allowance",
    "overtime": "Overtime",
    "casualOtRate": "Casual OT Rate",
    "grossSalary": "Gross salary",
    "providentFund": "Provident fund",
    "otherDeduction": "Other deduction",
    "salaryAdvance": "Salary advance",
    "noPay": "No pay",
    "totalDeduction": "Total deduction",
    "netPay": "Net pay",
}

_NUMERIC_SALARY_COMPONENT_KEYS = frozenset(_SALARY_COMPONENT_LABELS.keys())

_GROSS_COMPONENT_KEY_MAP = {
    PayCategory.OFFICE: (
        "basicSalary",
        "generalAllowance",
        "transportAllowance",
        "attendanceAllowance",
        "specialAllowance",
        "performanceBonus",
        "targetAllowance",
        "overtime",
    ),
    PayCategory.FACTORY: (
        "basicSalary",
        "generalAllowance",
        "transportAllowance",
        "attendanceAllowance",
        "specialAllowance",
        "performanceBonus",
        "production",
        "targetAllowance",
        "overtime",
    ),
    PayCategory.CASUAL: (
        "production",
        "generalAllowance",
        "transportAllowance",
        "attendanceAllowance",
        "specialAllowance",
        "performanceBonus",
        "targetAllowance",
        "overtime",
    ),
}

_GROSS_COMPONENT_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    "production": ("totalDaySalary", "production"),
}

_TARGET_ALLOWANCE_SPECIAL_REG_NO = "E005"
_TARGET_ALLOWANCE_SLABS: tuple[tuple[Decimal, Decimal, Decimal, Decimal], ...] = (
    (Decimal("300"), Decimal("350"), Decimal("4000"), Decimal("6000")),
    (Decimal("350"), Decimal("400"), Decimal("6000"), Decimal("7000")),
    (Decimal("400"), Decimal("450"), Decimal("8000"), Decimal("9000")),
    (Decimal("450"), Decimal("500"), Decimal("10000"), Decimal("11000")),
    (Decimal("500"), Decimal("550"), Decimal("12000"), Decimal("13000")),
    (Decimal("550"), Decimal("600"), Decimal("14000"), Decimal("15000")),
    (Decimal("600"), Decimal("650"), Decimal("16000"), Decimal("17000")),
)
_TARGET_ALLOWANCE_TOP_VALUES = (Decimal("16000"), Decimal("17000"))
_TARGET_ALLOWANCE_MIN_ATTENDANCE_DAYS = 18
_TARGET_ALLOWANCE_MACHINE_CODES: tuple[str, ...] = ("MCH-0001", "MCH-0002")

_CARRY_FORWARD_COMPONENT_KEYS = frozenset(
    {
        "basicSalary",
        "daySalary",
        "generalAllowance",
        "transportAllowance",
        "specialAllowance",
    }
)


def _extract_carry_forward_components(components: dict | None) -> dict[str, str]:
    """Return only the salary fields that should carry forward between months."""

    if not isinstance(components, dict):
        return {}

    carry_forward: dict[str, str] = {}

    for key in _CARRY_FORWARD_COMPONENT_KEYS:
        if key not in components:
            continue

        value = _clean_string(components.get(key))
        if value:
            carry_forward[key] = value

    return carry_forward


@bp.get("/salary")
@jwt_required()
def list_salary_records():
    month = _normalize_month(request.args.get("month"))
    if not month:
        return jsonify({"msg": "A valid month (YYYY-MM) is required."}), 400

    _ensure_schema()
    _ensure_tracking_tables()

    records = (
        TeamSalaryRecord.query.filter_by(month=month)
        .order_by(TeamSalaryRecord.team_member_id.asc())
        .all()
    )

    existing_member_ids = {record.team_member_id for record in records}
    prefilled_payloads: list[dict[str, object]] = []

    previous_records = (
        TeamSalaryRecord.query.filter(TeamSalaryRecord.month < month)
        .order_by(TeamSalaryRecord.team_member_id.asc(), TeamSalaryRecord.month.desc())
        .all()
    )

    seen_prefills: set[int] = set()

    for previous in previous_records:
        member_id = previous.team_member_id

        if member_id in existing_member_ids or member_id in seen_prefills:
            continue

        carry_components = _extract_carry_forward_components(previous.components)
        if not carry_components:
            continue

        prefilled_payloads.append(
            {
                "memberId": member_id,
                "month": month,
                "components": carry_components,
                "prefilled": True,
            }
        )
        seen_prefills.add(member_id)

    member_lookup: dict[int, TeamMember] = {}
    if existing_member_ids:
        members = TeamMember.query.filter(TeamMember.id.in_(existing_member_ids)).all()
        member_lookup = {member.id: member for member in members}

    records_changed = False
    for record in records:
        member = member_lookup.get(record.team_member_id, getattr(record, "team_member", None))
        updated_components, _ = _apply_computed_gross_component(member, record.components)
        original_components = record.components if isinstance(record.components, dict) else {}
        if original_components != updated_components:
            record.components = updated_components
            records_changed = True

    if records_changed:
        try:
            db.session.commit()
        except (DataError, IntegrityError, ProgrammingError, OperationalError) as exc:
            db.session.rollback()
            current_app.logger.warning(
                "Failed to persist gross salary updates: %s", exc
            )
            records = (
                TeamSalaryRecord.query.filter_by(month=month)
                .order_by(TeamSalaryRecord.team_member_id.asc())
                .all()
            )
            existing_member_ids = {record.team_member_id for record in records}

    component_sources: dict[int, dict | None] = {
        record.team_member_id: record.components for record in records
    }

    for payload in prefilled_payloads:
        member_id = payload.get("memberId")
        if isinstance(member_id, int):
            component_sources.setdefault(member_id, payload.get("components"))

    all_member_ids = set(existing_member_ids)
    for payload in prefilled_payloads:
        member_id = payload.get("memberId")
        if isinstance(member_id, int):
            all_member_ids.add(member_id)

    if all_member_ids:
        members = TeamMember.query.filter(TeamMember.id.in_(all_member_ids)).all()
        response_member_lookup = {member.id: member for member in members}
    else:
        response_member_lookup = {}

    serialized_records = salary_records_schema.dump(records)

    def _apply_component_to_payloads(
        payloads: list[dict[str, object]], component_key: str, values: dict[int, str]
    ) -> None:
        if not values:
            return

        for payload in payloads:
            if not isinstance(payload, dict):
                continue

            member_id = payload.get("memberId")
            if not isinstance(member_id, int):
                continue

            value = values.get(member_id)
            if value is None:
                continue

            components = payload.get("components")
            if isinstance(components, dict):
                updated = dict(components)
            else:
                updated = {}

            updated[component_key] = value
            payload["components"] = updated

    overtime_amounts = _compute_monthly_overtime_amounts(month, component_sources)
    _apply_component_to_payloads(serialized_records, "overtime", overtime_amounts)
    _apply_component_to_payloads(prefilled_payloads, "overtime", overtime_amounts)

    no_pay_amounts = _compute_no_pay_amounts(
        month,
        component_sources,
        response_member_lookup,
    )
    _apply_component_to_payloads(serialized_records, "noPay", no_pay_amounts)
    _apply_component_to_payloads(prefilled_payloads, "noPay", no_pay_amounts)

    def _update_payload_gross(
        payloads: list[dict[str, object]], member_lookup: dict[int, TeamMember]
    ) -> None:
        for payload in payloads:
            member_id = payload.get("memberId")
            if not isinstance(member_id, int):
                continue

            member = member_lookup.get(member_id)
            components = payload.get("components")
            updated_components, _ = _apply_computed_gross_component(member, components)
            payload["components"] = updated_components

    _update_payload_gross(serialized_records, response_member_lookup)
    _update_payload_gross(prefilled_payloads, response_member_lookup)

    target_allowances = _compute_target_allowance_amounts(month)
    allowance_payload = {
        str(member_id): _format_decimal_amount(amount)
        for member_id, amount in target_allowances.items()
    }

    return jsonify(
        {
            "month": month,
            "records": serialized_records + prefilled_payloads,
            "targetAllowances": allowance_payload,
        }
    )


@bp.put("/salary/<int:member_id>")
@jwt_required()
def upsert_salary_record(member_id: int):
    if not require_role(RoleEnum.admin, RoleEnum.production_manager):
        return jsonify({"msg": "Only administrators or production managers can update salary details."}), 403

    _ensure_schema()
    _ensure_tracking_tables()

    member = TeamMember.query.get_or_404(member_id)
    payload = request.get_json() or {}
    month = _normalize_month(payload.get("month"))
    if not month:
        return jsonify({"msg": "A valid month (YYYY-MM) is required."}), 400

    components_payload = payload.get("components")
    try:
        normalized_components = _normalize_salary_components(components_payload)
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    provided_keys = {
        key for key in normalized_components.keys() if key not in {"overtime", "grossSalary"}
    }
    computed_components, computed_overtime = _apply_computed_overtime_component(
        member, month, normalized_components
    )

    if provided_keys or computed_overtime > Decimal("0"):
        normalized_components = computed_components
    else:
        normalized_components = {}

    if normalized_components:
        normalized_components, _ = _apply_computed_gross_component(
            member, normalized_components
        )

    record = TeamSalaryRecord.query.filter_by(team_member_id=member.id, month=month).one_or_none()

    if normalized_components:
        if record is None:
            record = TeamSalaryRecord(team_member=member, month=month, components=normalized_components)
            db.session.add(record)
        else:
            record.components = normalized_components

        try:
            db.session.commit()
        except (DataError, IntegrityError, ProgrammingError, OperationalError) as exc:
            db.session.rollback()
            current_app.logger.warning("Failed to save salary record: %s", exc)
            return jsonify({"msg": "Unable to save salary for the selected month."}), 400

        return jsonify(salary_record_schema.dump(record))

    if record is None:
        return jsonify({"status": "deleted", "memberId": member.id, "month": month})

    db.session.delete(record)
    db.session.commit()
    return jsonify({"status": "deleted", "memberId": member.id, "month": month})
