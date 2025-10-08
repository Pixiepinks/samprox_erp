from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from extensions import db
from models import MaterialEntry, Job, RoleEnum
from schemas import MaterialEntrySchema
from routes.jobs import require_role

bp = Blueprint("materials", __name__, url_prefix="/api")
ms = MaterialEntrySchema()
mms = MaterialEntrySchema(many=True)

@bp.post("/jobs/<int:job_id>/materials")
@jwt_required()
def add_material(job_id):
    if not require_role(RoleEnum.maintenance_manager, RoleEnum.admin):
        return jsonify({"msg":"Only Maintenance Manager"}), 403
    Job.query.get_or_404(job_id)
    d = request.get_json()
    me = MaterialEntry(
        job_id=job_id,
        item_name=d["item_name"],
        qty=float(d["qty"]),
        unit_cost=float(d["unit_cost"]),
        note=d.get("note")
    )
    db.session.add(me); db.session.commit()
    return ms.jsonify(me), 201

@bp.get("/jobs/<int:job_id>/materials")
@jwt_required()
def list_materials(job_id):
    items = MaterialEntry.query.filter_by(job_id=job_id).all()
    return mms.jsonify(items)
