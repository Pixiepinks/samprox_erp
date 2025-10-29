from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import inspect, text
from sqlalchemy import types as sqltypes
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, ProgrammingError

from extensions import db
from models import (
    PayCategory,
    RoleEnum,
    TeamAttendanceRecord,
    TeamMember,
    TeamMemberStatus,
    TeamSalaryRecord,
    TeamWorkCalendarDay,
)  # keep import; not strictly required now
from routes.jobs import require_role
from schemas import (
    AttendanceRecordSchema,
    SalaryRecordSchema,
    TeamMemberSchema,
    WorkCalendarDaySchema,
)

bp = Blueprint("team", __name__, url_prefix="/api/team")

member_schema = TeamMemberSchema()
members_schema = TeamMemberSchema(many=True)
attendance_record_schema = AttendanceRecordSchema()
attendance_records_schema = AttendanceRecordSchema(many=True)
salary_record_schema = SalaryRecordSchema()
salary_records_schema = SalaryRecordSchema(many=True)
work_calendar_day_schema = WorkCalendarDaySchema()
work_calendar_days_schema = WorkCalendarDaySchema(many=True)

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


def _ensure_tracking_tables():
    """Ensure the attendance & salary tables exist."""

    try:
        engine = db.engine
    except RuntimeError:
        return

    for model in (TeamAttendanceRecord, TeamSalaryRecord):
        model.__table__.create(bind=engine, checkfirst=True)


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

        if entry_payload:
            normalized[date_key] = entry_payload

    return normalized


def _normalize_salary_components(components: dict | None) -> dict[str, str]:
    if not isinstance(components, dict):
        return {}

    normalized: dict[str, str] = {}

    for raw_key, raw_value in components.items():
        key = _clean_string(raw_key)
        if not key:
            continue

        value = _clean_string(raw_value)
        if value:
            normalized[key] = value

    return normalized


def _clean_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


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

    for key, label, maxlen in [
        ("nickname", "Nickname", 120),
        ("epf", "EPF number", 120),
        ("position", "Position", 120),
        ("image", "Profile image URL", 500),
        ("personalDetail", "Personal detail", None),
        ("assignments", "Assignments", None),
        ("trainingRecords", "Training records", None),
        ("employmentLog", "Employment log", None),
        ("files", "Files", None),
        ("assets", "Controlled assets", None),
    ]:
        if key in payload:
            try:
                value = _extract_string(payload.get(key), label=label, max_length=maxlen)
            except ValueError as exc:
                return jsonify({"msg": str(exc)}), 400
            attr = "image_url" if key == "image" else key
            setattr(member, attr if attr not in {"personalDetail", "trainingRecords", "employmentLog"} else {
                "personalDetail": "personal_detail",
                "trainingRecords": "training_records",
                "employmentLog": "employment_log",
            }[attr] if attr in {"personalDetail", "trainingRecords", "employmentLog"} else attr, value)

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

    return jsonify({"month": month, "records": attendance_records_schema.dump(records)})


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

    if normalized_entries:
        if record is None:
            record = TeamAttendanceRecord(team_member=member, month=month, entries=normalized_entries)
            db.session.add(record)
        else:
            record.entries = normalized_entries

        try:
            db.session.commit()
        except (DataError, IntegrityError, ProgrammingError, OperationalError) as exc:
            db.session.rollback()
            current_app.logger.warning("Failed to save attendance record: %s", exc)
            return jsonify({"msg": "Unable to save attendance for the selected month."}), 400

        return jsonify(attendance_record_schema.dump(record))

    if record is None:
        return jsonify({"status": "deleted", "memberId": member.id, "month": month})

    db.session.delete(record)
    db.session.commit()
    return jsonify({"status": "deleted", "memberId": member.id, "month": month})


_CARRY_FORWARD_COMPONENT_KEYS = frozenset(
    {
        "basicSalary",
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

    serialized_records = salary_records_schema.dump(records)

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

    return jsonify(
        {
            "month": month,
            "records": serialized_records + prefilled_payloads,
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
    normalized_components = _normalize_salary_components(components_payload)

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
