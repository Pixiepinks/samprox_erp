from flask import Blueprint, current_app, request, jsonify
from extensions import db, jwt
from models import User, RoleEnum
from flask_jwt_extended import (
    create_access_token,
    get_jwt,
    jwt_required,
    set_access_cookies,
    unset_jwt_cookies,
)
from sqlalchemy import func

from company_profiles import available_company_keys

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

@bp.post("/register")
@jwt_required()  # only admins can register
def register():
    claims = get_jwt()
    try:
        requester_role = RoleEnum(claims.get("role"))
    except (ValueError, TypeError):
        return jsonify({"msg": "Admins only"}), 403

    if requester_role != RoleEnum.admin:
        return jsonify({"msg": "Admins only"}), 403

    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    role = data.get("role")
    password = data.get("password")
    company_key = (data.get("company_key") or "").strip() or None

    if not email or not name or not role or not password:
        return jsonify({"msg": "Name, email, role, and password are required"}), 400

    try:
        role_enum = RoleEnum(role)
    except ValueError:
        return jsonify({"msg": "Invalid role"}), 400

    if company_key:
        allowed_keys = available_company_keys(current_app.config)
        if company_key not in allowed_keys:
            return jsonify({"msg": "Invalid company_key"}), 400

    u = User(name=name, email=email, role=role_enum, company_key=company_key)
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

    claims = {"role": u.role.value}
    if u.company_key:
        claims["company_key"] = u.company_key

    token = create_access_token(identity=str(u.id), additional_claims=claims)
    response = jsonify(
        access_token=token,
        user={
            "id": u.id,
            "name": u.name,
            "role": u.role.value,
            "company_key": u.company_key,
        },
    )
    set_access_cookies(response, token)
    return response


@bp.post("/logout")
def logout():
    response = jsonify({"msg": "Logged out"})
    unset_jwt_cookies(response)
    return response
