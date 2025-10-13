from flask import Blueprint, request, jsonify
from extensions import db, jwt
from models import User, RoleEnum
from flask_jwt_extended import create_access_token, jwt_required, get_jwt
from sqlalchemy import func

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

@bp.post("/register")
@jwt_required()  # only admins can register
def register():
    claims = get_jwt()
    if claims.get("role") != RoleEnum.admin:
        return jsonify({"msg": "Admins only"}), 403

    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    role = data.get("role")
    password = data.get("password")

    if not email or not name or not role or not password:
        return jsonify({"msg": "Name, email, role, and password are required"}), 400

    try:
        role_enum = RoleEnum(role)
    except ValueError:
        return jsonify({"msg": "Invalid role"}), 400

    u = User(name=name, email=email, role=role_enum)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return jsonify({"id": u.id})

@bp.post("/login")
def login():
    payload = request.get_json(silent=True)
    if not payload:
        payload = request.form.to_dict() if request.form else {}

    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""

    if not email or not password:
        return jsonify({"msg": "Email and password are required"}), 400

    u = User.query.filter(func.lower(User.email) == email).first()
    if not u or not u.check_password(password) or not u.active:
        return jsonify({"msg": "Invalid email or password"}), 401

    token = create_access_token(identity=u.id, additional_claims={"role": u.role})
    return jsonify(access_token=token, user={"id": u.id, "name": u.name, "role": u.role})
