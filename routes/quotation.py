from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from extensions import db
from models import Job, Quotation, RoleEnum
from schemas import QuotationSchema
from routes.jobs import require_role

bp = Blueprint("quotation", __name__, url_prefix="/api")
qs = QuotationSchema()

@bp.post("/jobs/<int:job_id>/quotation")
@jwt_required()
def upsert_quotation(job_id):
    if not require_role(RoleEnum.maintenance_manager, RoleEnum.admin):
        return jsonify({"msg":"Only Maintenance Manager"}), 403
    job = Job.query.get_or_404(job_id)
    data = request.get_json()
    q = job.quotation or Quotation(job_id=job.id)
    q.labor_estimate_hours = data.get("labor_estimate_hours", 0)
    q.labor_rate = data.get("labor_rate", 0)
    q.material_estimate_cost = data.get("material_estimate_cost", 0)
    q.notes = data.get("notes")
    db.session.add(q)
    db.session.commit()
    return qs.jsonify(q)

@bp.get("/jobs/<int:job_id>/quotation")
@jwt_required()
def get_quotation(job_id):
    job = Job.query.get_or_404(job_id)
    return qs.jsonify(job.quotation) if job.quotation else (jsonify({}), 204)
