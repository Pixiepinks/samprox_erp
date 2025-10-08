from flask import Blueprint, request, jsonify
from extensions import db, jwt
from models import User, RoleEnum
from flask_jwt_extended import create_access_token, jwt_required, get_jwt
from datetime import timedelta

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

@bp.post("/register")
@jwt_required()  # only admins can register
def register():
    claims = get_jwt()
    if claims.get("role") != RoleEnum.admin:
        return jsonify({"msg":"Admins only"}), 403
    data = request.get_json()
    u = User(name=data["name"], email=data["email"], role=RoleEnum(data["role"]))
    u.set_password(data["password"])
    db.session.add(u)
    db.session.commit()
    return jsonify({"id": u.id})

@bp.post("/login")
def login():
    data = request.get_json()
    u = User.query.filter_by(email=data["email"]).first()
    if not u or not u.check_password(data["password"]) or not u.active:
        return jsonify({"msg":"Bad credentials"}), 401
    token = create_access_token(identity=u.id, additional_claims={"role": u.role})
    return jsonify(access_token=token, user={"id":u.id,"name":u.name,"role":u.role})
