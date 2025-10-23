from datetime import date, datetime
import re

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import inspect, text
from sqlalchemy import types as sqltypes
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, ProgrammingError

from extensions import db
from models import RoleEnum, TeamMember, TeamMemberStatus  # keep import; not strictly required now
from routes.jobs import require_role
from schemas import TeamMemberSchema

bp = Blueprint("team", __name__, url_prefix="/api/team")

member_schema = TeamMemberSchema()
members_schema = TeamMemberSchema(many=True)


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

    if "image_url" not in columns:
        statements.append("ALTER TABLE team_member ADD COLUMN image_url VARCHAR(500)")

    dialect = engine.dialect.name

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

    if statements or status_fixes:
        with engine.begin() as conn:
            for stmt in status_fixes:
                try:
                    conn.execute(text(stmt))
                except (ProgrammingError, OperationalError, DataError):
                    current_app.logger.debug("Status normalization statement failed or already applied: %s", stmt)
            for stmt in statements:
                try:
                    conn.execute(text(stmt))
                except (ProgrammingError, OperationalError, DataError):
                    current_app.logger.debug("Schema statement likely already applied: %s", stmt)


def _clean_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


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

    # --- Create model ---
    member = TeamMember(
        reg_number=reg_number,
        name=name,
        nickname=nickname,
        epf=epf,
        position=position,
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
