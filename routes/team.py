from datetime import date

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

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

    if statements:
        with engine.begin() as connection:
            for statement in statements:
                try:
                    connection.execute(text(statement))
                except (ProgrammingError, OperationalError):
                    # If another process created the column in the meantime we can ignore the error.
                    current_app.logger.debug("Schema statement failed (likely already applied): %s", statement)


def _parse_join_date(value, *, required: bool) -> date | None:
    if not value:
        if required:
            raise ValueError("Date of Join is required.")
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid date for joinDate.") from exc


def _parse_status(value) -> TeamMemberStatus:
    if not value:
        return TeamMemberStatus.ACTIVE
    try:
        return TeamMemberStatus(value)
    except ValueError as exc:
        valid_values = ", ".join(status.value for status in TeamMemberStatus)
        raise ValueError(f"Status must be one of: {valid_values}.") from exc


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
    if not require_role(RoleEnum.admin):
        return jsonify({"msg": "Only administrators can register team members."}), 403

    _ensure_schema()

    payload = request.get_json() or {}

    reg_number = (payload.get("regNumber") or "").strip()
    if not reg_number:
        return jsonify({"msg": "Registration number is required."}), 400

    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"msg": "Name is required."}), 400

    try:
        join_date = _parse_join_date(payload.get("joinDate"), required=True)
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    try:
        status = _parse_status(payload.get("status"))
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400

    member = TeamMember(
        reg_number=reg_number,
        name=name,
        nickname=(payload.get("nickname") or "").strip() or None,
        epf=(payload.get("epf") or "").strip() or None,
        position=(payload.get("position") or "").strip() or None,
        join_date=join_date,
        status=status,
        image_url=(payload.get("image") or "").strip() or None,
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
        name = (payload.get("name") or "").strip()
        if not name:
            return jsonify({"msg": "Name is required."}), 400
        member.name = name

    if "nickname" in payload:
        member.nickname = (payload.get("nickname") or "").strip() or None

    if "epf" in payload:
        member.epf = (payload.get("epf") or "").strip() or None

    if "position" in payload:
        member.position = (payload.get("position") or "").strip() or None

    if "image" in payload:
        member.image_url = (payload.get("image") or "").strip() or None

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
    except (ProgrammingError, OperationalError):
        db.session.rollback()
        current_app.logger.exception("Failed to update team member due to schema issues.")
        _ensure_schema()
        db.session.add(member)
        db.session.commit()
    return jsonify(member_schema.dump(member))
