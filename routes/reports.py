from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import func
from extensions import db
from models import Job, LaborEntry, MaterialEntry, JobStatus

bp = Blueprint("reports", __name__, url_prefix="/api/reports")

@bp.get("/costs")
@jwt_required()
def job_costs():
    job_id = int(request.args["job_id"])
    labor = db.session.query(func.coalesce(func.sum(LaborEntry.hours*LaborEntry.rate),0)).filter_by(job_id=job_id).scalar()
    materials = db.session.query(func.coalesce(func.sum(MaterialEntry.qty*MaterialEntry.unit_cost),0)).filter_by(job_id=job_id).scalar()
    return jsonify({"job_id": job_id, "labor_cost": float(labor), "material_cost": float(materials), "total_cost": float(labor+materials)})
