from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from extensions import db
from models import Job, User, JobStatus, RoleEnum
from schemas import JobSchema
from datetime import date
from sqlalchemy import or_

bp = Blueprint("jobs", __name__, url_prefix="/api/jobs")
job_schema = JobSchema()
jobs_schema = JobSchema(many=True)

def require_role(*roles):
    claims = get_jwt()
    try:
        current_role = RoleEnum(claims.get("role"))
    except (ValueError, TypeError):
        return False
    return current_role in roles

@bp.post("")
@jwt_required()
def create_job():
    if not require_role(RoleEnum.production_manager, RoleEnum.admin):
        return jsonify({"msg":"Only Production Manager can create"}), 403
    try:
        creator_id = int(get_jwt().get("sub"))
    except (TypeError, ValueError):
        return jsonify({"msg": "Invalid token subject"}), 422
    data = request.get_json()
    j = Job(
        code=data["code"],
        title=data["title"],
        description=data.get("description"),
        priority=data.get("priority","Normal"),
        location=data.get("location"),
        expected_completion_date=date.fromisoformat(data["expected_completion_date"]) if data.get("expected_completion_date") else None,
        created_by_id=creator_id
    )
    db.session.add(j)
    db.session.commit()
    return jsonify(job_schema.dump(j)), 201

@bp.get("")
@jwt_required()
def list_jobs():
    q = Job.query
    status = request.args.get("status")
    if status: q = q.filter(Job.status==JobStatus(status))
    assigned_to = request.args.get("assigned_to")
    if assigned_to: q = q.filter(Job.assigned_to_id==int(assigned_to))
    text = request.args.get("q")
    if text:
        q = q.filter(or_(Job.title.ilike(f"%{text}%"), Job.description.ilike(f"%{text}%"), Job.code.ilike(f"%{text}%")))
    return jsonify(jobs_schema.dump(q.order_by(Job.created_at.desc()).all()))

@bp.get("/<int:job_id>")
@jwt_required()
def get_job(job_id):
    j = Job.query.get_or_404(job_id)
    return jsonify(job_schema.dump(j))

@bp.patch("/<int:job_id>")
@jwt_required()
def update_job(job_id):
    j = Job.query.get_or_404(job_id)
    claims = get_jwt()
    try:
        role = RoleEnum(claims.get("role"))
    except (ValueError, TypeError):
        return jsonify({"msg": "Invalid role"}), 403
    try:
        actor_id = int(claims.get("sub"))
    except (TypeError, ValueError):
        return jsonify({"msg": "Invalid token subject"}), 422
    data = request.get_json()

    if "status" in data:
        new_status = JobStatus(data["status"])
        can_change_status = role in [RoleEnum.maintenance_manager, RoleEnum.admin]

        if not can_change_status:
            can_change_status = (
                role == RoleEnum.production_manager
                and j.status == JobStatus.NEW
                and new_status == JobStatus.ACCEPTED
            )

        if not can_change_status:
            return jsonify({"msg": "Only Maintenance Manager can change status"}), 403

        j.status = new_status
        if j.status == JobStatus.ACCEPTED and not j.assigned_to_id:
            # auto-assign to the manager performing action
            j.assigned_to_id = actor_id

    if "assigned_to_id" in data and role in [RoleEnum.maintenance_manager, RoleEnum.admin]:
        j.assigned_to_id = data["assigned_to_id"]

    if "expected_completion_date" in data:
        j.expected_completion_date = None if not data["expected_completion_date"] else \
            date.fromisoformat(data["expected_completion_date"])

    if "progress_pct_manual" in data and role in [RoleEnum.maintenance_manager, RoleEnum.admin]:
        j.progress_pct_manual = int(data["progress_pct_manual"])

    db.session.commit()
    return jsonify(job_schema.dump(j))

@bp.post("/<int:job_id>/complete")
@jwt_required()
def complete_job(job_id):
    if not require_role(RoleEnum.maintenance_manager, RoleEnum.admin):
        return jsonify({"msg":"Only Maintenance Manager can complete"}), 403
    j = Job.query.get_or_404(job_id)
    j.status = JobStatus.COMPLETED
    j.completed_date = date.today()
    db.session.commit()
    return jsonify(job_schema.dump(j))
