from datetime import date, datetime
import re

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, ProgrammingError

from extensions import db
from models import RoleEnum, TeamMember  # TeamMemberStatus not needed when storing strings
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
    "active": "Active",
    "inactive": "Inactive",
    "on leave": "On Leave",
    "on_leave": "On Leave",
    "on-leave": "On Leave",
}


def _normalize_status(value) -> str:
    """Return the exact DB enum label ('Active', 'On Leave', 'Inactive')."""
    if value is None:
        return "Active"
    return _STATUS_MAP.get(str(value).strip().lower(), "Active")


def _parse_join_date(value, *, required: bool) -> date | None:
    """Accept YYYY-MM-DD or MM/DD/YYYY (and a few common variations)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    s = _clean_string(value)
    if not s:
        if required:
            raise ValueError("Date of Join is required.")
        return None

    # Fast paths first
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    # Common separators swapped or extra spaces
    s2 = re.sub(r"\s+", " ", s.replace("\\", "/").replace(".", "-").replace("/", "/")).strip()

    for candidate in {s, s2, s2.replace("/", "-")}:  # try 2025/10/23 or 2025-10-23
        for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue

    # Last resort: ISO parser (handles timestamps like 2025-10-23T00:00:00Z)
    iso_candidate = s.replace("/", "-")
    if iso_candidate.endswith("Z"):
        iso_candidate = f"{iso_candidate[:-1]}+00:00"
    try:
        return datetime.fromisoformat(iso_candidate).date()
    except ValueError:
        pass

    raise ValueError("Invalid date for joinDate. Please use the YYYY-MM-DD format.")


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
        reg_number = _extract_string(
            payload.get("regNumber"), label="Registration number", required=True, max_length=40
        )
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

    # IMPORTANT: store the exact DB label as a STRING (not Enum) to avoid 'ACTIVE' vs 'Active' issues
    status = _normalize_status(payload.get("status"))
    if status not in {"Active", "Inactive", "On Leave"}:
        return jsonify({"msg": "Status must be one of: Active, On Leave, Inactive."}), 400

    # --- Create model ---
    member = TeamMember(
        reg_number=reg_number,
        name=name,
        nickname=nickname,
        epf=epf,
        position=position,
        join_date=join_date,
        status=status,  # pass normalized string, not Enum
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
            name = _extract_string(payload.get("name"), label="Name", required=True, max_length=200)
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400
        member.name = name

    if "nickname" in payload:
        try:
            member.nickname = _extract_string(payload.get("nickname"), label="Nickname", max_length=120)
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "epf" in payload:
        try:
            member.epf = _extract_string(payload.get("epf"), label="EPF number", max_length=120)
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "position" in payload:
        try:
            member.position = _extract_string(payload.get("position"), label="Position", max_length=120)
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "image" in payload:
        try:
            member.image_url = _extract_string(payload.get("image"), label="Profile image URL", max_length=500)
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "personalDetail" in payload:
        try:
            member.personal_detail = _extract_string(payload.get("personalDetail"), label="Personal detail")
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "assignments" in payload:
        try:
            member.assignments = _extract_string(payload.get("assignments"), label="Assignments")
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "trainingRecords" in payload:
        try:
            member.training_records = _extract_string(payload.get("trainingRecords"), label="Training records")
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "employmentLog" in payload:
        try:
            member.employment_log = _extract_string(payload.get("employmentLog"), label="Employment log")
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "files" in payload:
        try:
            member.files = _extract_string(payload.get("files"), label="Files")
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "assets" in payload:
        try:
            member.assets = _extract_string(payload.get("assets"), label="Controlled assets")
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400

    if "joinDate" in payload:
        try:
            join_date = _parse_join_date(payload.get("joinDate"), required=True)
        except ValueError as exc:
            return jsonify({"msg": str(exc)}), 400
        member.join_date = join_date

    if "status" in payload:
        s = _normalize_status(payload.get("status"))
        if s not in {"Active", "Inactive", "On Leave"}:
            return jsonify({"msg": "Status must be one of: Active, On Leave, Inactive."}), 400
        member.status = s  # store DB label string

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
