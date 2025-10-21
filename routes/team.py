from datetime import date, datetime
import re

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, ProgrammingError

from extensions import db
from models import RoleEnum, TeamMember, TeamMemberStatus
from routes.jobs import require_role
from schemas import TeamMemberSchema

bp = Blueprint("team", __name__, url_prefix="/api/team")

member_schema = TeamMemberSchema()
members_schema = TeamMemberSchema(many=True)


def _ensure_schema():
    """Ensure the ``team_member`` table has the expected structure."""

    try:
        engine = db.engine
    except RuntimeError:
        # No application context – nothing we can do here.
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "team_member" not in tables:
        # Create the table if it has not been provisioned yet.
        TeamMember.__table__.create(bind=engine, checkfirst=True)
        inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("team_member")}

    statements: list[str] = []

    if "image_url" not in columns:
        statements.append("ALTER TABLE team_member ADD COLUMN image_url VARCHAR(500)")

    dialect = engine.dialect.name

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

    for column_name, column_type in optional_text_columns.items():
        if column_name not in columns:
            statements.append(f"ALTER TABLE team_member ADD COLUMN {column_name} {column_type}")

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                try:
                    connection.execute(text(statement))
                except (ProgrammingError, OperationalError):
                    # If another process created the column in the meantime we can ignore the error.
                    current_app.logger.debug("Schema statement failed (likely already applied): %s", statement)


def _clean_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _extract_string(
    value,
    *,
    label: str,
    required: bool = False,
    max_length: int | None = None,
) -> str | None:
    text = _clean_string(value)
    if not text:
        if required:
            raise ValueError(f"{label} is required.")
        return None
    if max_length is not None and len(text) > max_length:
        raise ValueError(f"{label} must be at most {max_length} characters.")
    return text


def _parse_join_date(value, *, required: bool) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = _clean_string(value)
    if not text:
        if required:
            raise ValueError("Date of Join is required.")
        return None

    normalized = text.replace("\\", "/")
    normalized = re.sub(r"[\u2013\u2014\u2212]", "-", normalized)
    normalized = re.sub(r",", " ", normalized)
    normalized = re.sub(r"(\d)\s*\.\s*(\d)", r"\1-\2", normalized)
    normalized = re.sub(r"(?<=\d)(st|nd|rd|th)(?=\b)", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(of|the)\b", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"^[^0-9A-Za-z]+|[^0-9A-Za-z]+$", "", normalized)
    collapsed = re.sub(r"\s*([/\-])\s*", r"\1", normalized)
    collapsed = re.sub(r"^[^0-9A-Za-z]+|[^0-9A-Za-z]+$", "", collapsed)

    def _attempt(year: int, month: int, day: int) -> date | None:
        try:
            return date(year, month, day)
        except ValueError:
            return None

    iso_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", collapsed)
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        candidate = _attempt(year, month, day)
        if candidate:
            return candidate

    slash_iso_match = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", collapsed)
    if slash_iso_match:
        year, month, day = map(int, slash_iso_match.groups())
        candidate = _attempt(year, month, day)
        if candidate:
            return candidate

    iso_datetime_candidate = collapsed.replace("/", "-")
    if iso_datetime_candidate.endswith("Z"):
        iso_datetime_candidate = f"{iso_datetime_candidate[:-1]}+00:00"
    try:
        return datetime.fromisoformat(iso_datetime_candidate).date()
    except ValueError:
        pass

    year_last_match = re.fullmatch(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", collapsed)
    if year_last_match:
        first, second, year_str = year_last_match.groups()
        year = int(year_str)
        first_num = int(first)
        second_num = int(second)
        candidates: list[tuple[int, int]] = []

        if first_num > 12 and second_num <= 12:
            candidates.append((second_num, first_num))
        elif second_num > 12 and first_num <= 12:
            candidates.append((first_num, second_num))
        else:
            candidates.extend(((second_num, first_num), (first_num, second_num)))

        for month, day in candidates:
            candidate = _attempt(year, month, day)
            if candidate:
                return candidate

    month_lookup = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }

    def _month_from_name(name: str) -> int | None:
        cleaned = name.strip().rstrip(".")
        return month_lookup.get(cleaned.lower())

    day_month_year = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+\.?)\s+(\d{4})", normalized)
    if day_month_year:
        day_str, month_name, year_str = day_month_year.groups()
        month = _month_from_name(month_name)
        if month:
            candidate = _attempt(int(year_str), month, int(day_str))
            if candidate:
                return candidate

    month_day_year = re.fullmatch(r"([A-Za-z]+\.?)\s+(\d{1,2}),?\s+(\d{4})", normalized)
    if month_day_year:
        month_name, day_str, year_str = month_day_year.groups()
        month = _month_from_name(month_name)
        if month:
            candidate = _attempt(int(year_str), month, int(day_str))
            if candidate:
                return candidate

    year_month_day = re.fullmatch(r"(\d{4})\s+([A-Za-z]+\.?)\s+(\d{1,2})", normalized)
    if year_month_day:
        year_str, month_name, day_str = year_month_day.groups()
        month = _month_from_name(month_name)
        if month:
            candidate = _attempt(int(year_str), month, int(day_str))
            if candidate:
                return candidate

    accepted_formats = (
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%Y/%m/%d",
        "%Y-%m-%d",
        "%d %m %Y",
        "%m %d %Y",
        "%Y %m %d",
        "%d %b %Y",
        "%d %B %Y",
        "%Y-%b-%d",
        "%Y-%B-%d",
    )

    for candidate_text in {collapsed, normalized, text}:
        for fmt in accepted_formats:
            try:
                return datetime.strptime(candidate_text, fmt).date()
            except ValueError:
                continue

    raise ValueError("Invalid date for joinDate. Please use the YYYY-MM-DD format.")


def _parse_status(value) -> TeamMemberStatus:
    if isinstance(value, TeamMemberStatus):
        return value
    text = _clean_string(value)
    if not text:
        return TeamMemberStatus.ACTIVE
    try:
        return TeamMemberStatus(text)
    except ValueError as exc:
        valid_values = ", ".join(status.value for status in TeamMemberStatus)
        raise ValueError(f"Status must be one of: {valid_values}.") from exc


def _data_error_message(exc: DataError, *, fallback: str) -> str:
    detail = ""
    origin = getattr(exc, "orig", None)
    if origin is not None:
        detail = str(origin)
    elif exc.args:
        detail = " ".join(str(arg) for arg in exc.args if arg)

    lowered = detail.lower()

    if any(keyword in lowered for keyword in ("invalid", "incorrect", "out of range")) and "date" in lowered:
        return "Invalid date for joinDate. Please use the YYYY-MM-DD format."

    if "isoformat" in lowered and "date" in lowered:
        return "Invalid date for joinDate. Please use the YYYY-MM-DD format."

    return fallback


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

    try:
        reg_number = _extract_string(
            payload.get("regNumber"),
            label="Registration number",
            required=True,
            max_length=40,
        )
        name = _extract_string(
            payload.get("name"),
            label="Name",
            required=True,
            max_length=200,
        )
        nickname = _extract_string(
            payload.get("nickname"),
            label="Nickname",
            max_length=120,
        )
        epf = _extract_string(
            payload.get("epf"),
            label="EPF number",
            max_length=120,
        )
        position = _extract_string(
            payload.get("position"),
            label="Position",
            max_length=120,
        )
        image_url = _extract_string(
            payload.get("image"),
            label="Profile image URL",
            max_length=500,
        )
        personal_detail = _extract_string(
            payload.get("personalDetail"),
            label="Personal detail",
        )
        assignments = _extract_string(
            payload.get("assignments"),
            label="Assignments",
        )
        training_records = _extract_string(
            payload.get("trainingRecords"),
            label="Training records",
        )
        employment_log = _extract_string(
            payload.get("employmentLog"),
            label="Employment log",
        )
        files_value = _extract_string(
            payload.get("files"),
            label="Files",
        )
        assets_value = _extract_string(
            payload.get("assets"),
            label="Controlled assets",
        )
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    try:
        join_date = _parse_join_date(payload.get("joinDate"), required=False)
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    if join_date is None:
        join_date = date.today()

    try:
        status = _parse_status(payload.get("status"))
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    member = TeamMember(
        reg_number=reg_number,
        name=name,
        nickname=nickname,
        epf=epf,
        position=position,
        join_date=join_date,
        status=status,
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
        return (
            jsonify({"msg": f"Registration number {reg_number} already exists."}),
            409,
        )
    except DataError as exc:
        db.session.rollback()
        message = _data_error_message(
            exc,
            fallback="Unable to register member. One or more fields exceed the allowed length.",
        )
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
            name = _extract_string(
                payload.get("name"),
                label="Name",
                required=True,
                max_length=200,
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400
        member.name = name

    if "nickname" in payload:
        try:
            member.nickname = _extract_string(
                payload.get("nickname"),
                label="Nickname",
                max_length=120,
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "epf" in payload:
        try:
            member.epf = _extract_string(
                payload.get("epf"),
                label="EPF number",
                max_length=120,
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "position" in payload:
        try:
            member.position = _extract_string(
                payload.get("position"),
                label="Position",
                max_length=120,
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "image" in payload:
        try:
            member.image_url = _extract_string(
                payload.get("image"),
                label="Profile image URL",
                max_length=500,
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "personalDetail" in payload:
        try:
            member.personal_detail = _extract_string(
                payload.get("personalDetail"),
                label="Personal detail",
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "assignments" in payload:
        try:
            member.assignments = _extract_string(
                payload.get("assignments"),
                label="Assignments",
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "trainingRecords" in payload:
        try:
            member.training_records = _extract_string(
                payload.get("trainingRecords"),
                label="Training records",
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "employmentLog" in payload:
        try:
            member.employment_log = _extract_string(
                payload.get("employmentLog"),
                label="Employment log",
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "files" in payload:
        try:
            member.files = _extract_string(
                payload.get("files"),
                label="Files",
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "assets" in payload:
        try:
            member.assets = _extract_string(
                payload.get("assets"),
                label="Controlled assets",
            )
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "joinDate" in payload:
        try:
            join_date = _parse_join_date(payload.get("joinDate"), required=True)
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400
        member.join_date = join_date

    if "status" in payload:
        try:
            member.status = _parse_status(payload.get("status"))
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    try:
        db.session.commit()
    except DataError as exc:
        db.session.rollback()
        message = _data_error_message(
            exc,
            fallback="Unable to update member. One or more fields exceed the allowed length.",
        )
        current_app.logger.warning("Failed to update team member due to data error: %s", exc)
        return jsonify({"msg": message}), 400
    except (ProgrammingError, OperationalError):
        db.session.rollback()
        current_app.logger.exception("Failed to update team member due to schema issues.")
        _ensure_schema()
        db.session.add(member)
        db.session.commit()
    return jsonify(member_schema.dump(member))
