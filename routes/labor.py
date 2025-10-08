from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from extensions import db
from models import LaborEntry, Job, RoleEnum
from schemas import LaborEntrySchema
from datetime import date
from routes.jobs import require_role

bp = Blueprint("labor", __name__, url_prefix="/api")
ls = LaborEntrySchema()
lms = LaborEntrySchema(many=True)

@bp.post("/jobs/<int:job_id>/labor")
@jwt_required()
def add_labor(job_id):
    if not require_role(RoleEnum.maintenance_manager, RoleEnum.admin):
        return jsonify({"msg":"Only Maintenance Manager"}), 403
    Job.query.get_or_404(job_id)
    d = request.get_json()
    le = LaborEntry(
        job_id=job_id,
        user_id=d["user_id"],
        date=date.fromisoformat(d["date"]),
        hours=float(d["hours"]),
        rate=float(d["rate"]),
        note=d.get("note")
    )
    db.session.add(le); db.session.commit()
    return ls.jsonify(le), 201

@bp.get("/jobs/<int:job_id>/labor")
@jwt_required()
def list_labor(job_id):
    items = LaborEntry.query.filter_by(job_id=job_id).all()
    return lms.jsonify(items)
