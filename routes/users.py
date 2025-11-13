"""Administrative endpoints for managing application users."""

from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy import asc, func

from extensions import db
from models import RoleEnum, User


bp = Blueprint("users", __name__, url_prefix="/api/users")


def _require_admin() -> tuple[RoleEnum | None, Any]:
    """Ensure the current request is authenticated as an admin user."""

    claims = get_jwt()
    try:
        role = RoleEnum(claims.get("role"))
    except (ValueError, TypeError):
        role = None

    if role != RoleEnum.admin:
        return None, (jsonify({"msg": "Admins only"}), 403)

    return role, None


def _normalise_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _serialise_user(user: User) -> Dict[str, Any]:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role.value,
        "active": bool(user.active),
    }


@bp.get("")
@bp.get("/")
@jwt_required()
def list_users():
    """Return a list of all users for administrative management."""

    _, error = _require_admin()
    if error:
        return error

    users = User.query.order_by(asc(User.name)).all()
    return jsonify([_serialise_user(user) for user in users])


@bp.post("")
@bp.post("/")
@jwt_required()
def create_user():
    """Create a new application user."""

    _, error = _require_admin()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    email = _normalise_email(payload.get("email"))
    password = payload.get("password") or ""
    role_value = payload.get("role")
    active = bool(payload.get("active", True))

    if not name or not email or not password or not role_value:
        return jsonify({"msg": "Name, email, role, and password are required"}), 400

    try:
        role = RoleEnum(role_value)
    except ValueError:
        return jsonify({"msg": "Invalid role"}), 400

    existing = (
        User.query.filter(func.lower(User.email) == email.lower()).first()
        if email
        else None
    )
    if existing:
        return jsonify({"msg": "Email already registered"}), 400

    user = User(name=name, email=email, role=role, active=active)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    return jsonify(_serialise_user(user)), 201


@bp.put("/<int:user_id>")
@jwt_required()
def update_user(user_id: int):
    """Update the selected user's details."""

    _, error = _require_admin()
    if error:
        return error

    user = User.query.get(user_id)
    if not user:
        return jsonify({"msg": "User not found"}), 404

    payload = request.get_json(silent=True) or {}

    name = (payload.get("name") or user.name or "").strip()
    email = _normalise_email(payload.get("email")) or user.email
    role_value = payload.get("role") or user.role.value
    active = bool(payload.get("active", user.active))
    password = (payload.get("password") or "").strip()

    if not name or not email or not role_value:
        return jsonify({"msg": "Name, email, and role are required"}), 400

    try:
        role = RoleEnum(role_value)
    except ValueError:
        return jsonify({"msg": "Invalid role"}), 400

    existing = (
        User.query.filter(func.lower(User.email) == email.lower(), User.id != user.id)
        .first()
    )
    if existing:
        return jsonify({"msg": "Email already registered"}), 400

    user.name = name
    user.email = email
    user.role = role
    user.active = active

    if password:
        user.set_password(password)

    db.session.commit()

    return jsonify(_serialise_user(user))
