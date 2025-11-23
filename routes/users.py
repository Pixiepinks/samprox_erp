"""Administrative endpoints for managing application users."""

from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy import asc, func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import RoleEnum, User
from company_profiles import available_company_keys, resolve_company_profile


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


def _validate_company_key(value: Any) -> tuple[str | None, tuple[Any, int] | None]:
    if value is None:
        return None, None

    key = value.strip() if isinstance(value, str) else None
    if key is None:
        return None, (jsonify({"msg": "Invalid company_key"}), 400)

    if not key:
        return None, None

    allowed = available_company_keys(current_app.config)
    if key not in allowed:
        return None, (jsonify({"msg": "Invalid company_key"}), 400)

    return key, None


def _serialise_user(user: User) -> Dict[str, Any]:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role.value,
        "active": bool(user.active),
        "company_key": user.company_key,
    }


@bp.get("/companies")
@jwt_required()
def list_companies():
    """Return available companies for assigning users."""

    _, error = _require_admin()
    if error:
        return error

    keys = available_company_keys(current_app.config)
    companies: list[Dict[str, Any]] = []

    for key in keys:
        profile = resolve_company_profile(current_app.config, key)
        companies.append({"key": key, "name": profile.get("name") or key})

    return jsonify(companies)


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
    company_key, company_error = _validate_company_key(payload.get("company_key"))

    if not name or not email or not password or not role_value:
        return jsonify({"msg": "Name, email, role, and password are required"}), 400

    if company_error:
        return company_error

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

    user = User(
        name=name,
        email=email,
        role=role,
        active=active,
        company_key=company_key,
    )
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
    company_raw = payload.get("company_key", user.company_key)
    company_key, company_error = _validate_company_key(company_raw)
    if company_raw is None:
        company_key = user.company_key

    if not name or not email or not role_value:
        return jsonify({"msg": "Name, email, and role are required"}), 400

    try:
        role = RoleEnum(role_value)
    except ValueError:
        return jsonify({"msg": "Invalid role"}), 400

    if company_error:
        return company_error

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
    user.company_key = company_key

    if password:
        user.set_password(password)

    db.session.commit()

    return jsonify(_serialise_user(user))


@bp.delete("/<int:user_id>")
@jwt_required()
def delete_user(user_id: int):
    """Delete the selected user from the system."""

    _, error = _require_admin()
    if error:
        return error

    current_identity = get_jwt_identity()
    try:
        current_user_id = int(current_identity)
    except (TypeError, ValueError):
        current_user_id = None

    user = User.query.get(user_id)
    if not user:
        return jsonify({"msg": "User not found"}), 404

    if current_user_id is not None and user.id == current_user_id:
        return jsonify({"msg": "You cannot delete your own account."}), 400

    if user.role == RoleEnum.admin:
        remaining_admins = (
            User.query.filter(User.role == RoleEnum.admin, User.id != user.id).count()
        )
        if remaining_admins == 0:
            return jsonify({"msg": "Cannot delete the last admin user."}), 400

    try:
        db.session.delete(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return (
            jsonify(
                {
                    "msg": "Unable to delete user because they are linked to other records."
                }
            ),
            400,
        )

    return jsonify({"msg": "User deleted"})
