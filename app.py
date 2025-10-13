import os
from typing import Optional, Tuple

import click
from flask import Flask, jsonify
from sqlalchemy import func
from sqlalchemy.exc import OperationalError, ProgrammingError

from config import Config
from extensions import db, migrate, jwt
from models import User, RoleEnum
from routes import auth, jobs, quotation, labor, materials, reports, ui

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)

    @jwt.additional_claims_loader
    def add_claims(identity):
        u = User.query.get(identity)
        return {"role": u.role if u else None}

    app.register_blueprint(auth.bp)
    app.register_blueprint(jobs.bp)
    app.register_blueprint(quotation.bp)
    app.register_blueprint(labor.bp)
    app.register_blueprint(materials.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(ui.bp)

    @app.get("/api/health")
    def health(): return jsonify({"ok": True})

    return app

app = create_app()


def _normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def _ensure_admin_user(
    flask_app=None,
    *,
    email: Optional[str] = None,
    password: Optional[str] = None,
    ensure_if_missing: bool = True,
    force_reset: bool = False,
) -> Tuple[str, str]:
    """Ensure an admin user exists and optionally reset its password.

    Returns a tuple of (status, normalized_email) where status is one of
    ``{"created", "reset", "updated", "skipped"}``.
    """

    target_app = flask_app or app
    if target_app is None:
        return "skipped", _normalize_email(email)

    normalized_email = _normalize_email(email or os.getenv("ADMIN_EMAIL", "admin@samprox.lk"))
    password = password or os.getenv("ADMIN_PASSWORD", "Admin@123")

    with target_app.app_context():
        try:
            admin = User.query.filter(func.lower(User.email) == normalized_email).first()
        except (OperationalError, ProgrammingError):
            # Tables might not be ready yet (e.g. before migrations run)
            return "skipped", normalized_email

        if admin:
            status = "skipped"
            if admin.role != RoleEnum.admin:
                admin.role = RoleEnum.admin
                status = "updated"
            if force_reset:
                admin.set_password(password)
                status = "reset"

            if status != "skipped":
                db.session.commit()
            return status, normalized_email

        if not ensure_if_missing:
            return "skipped", normalized_email

        if not force_reset:
            # Avoid creating duplicate admins when one already exists
            existing_admin = User.query.filter_by(role=RoleEnum.admin).first()
            if existing_admin:
                return "skipped", normalized_email

        admin = User(name="Admin", email=normalized_email, role=RoleEnum.admin, active=True)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        return "created", normalized_email


def _bootstrap_admin_user():
    status, normalized_email = _ensure_admin_user(force_reset=os.getenv("RUN_SEED_ADMIN") == "1")
    if status == "created":
        print(f"✅ Admin created: {normalized_email}")
    elif status == "reset":
        print(f"✅ Admin password reset: {normalized_email}")
    elif status == "updated":
        print(f"✅ Admin role updated: {normalized_email}")


# Call the hook at startup (idempotent)
_bootstrap_admin_user()

# ---- CLI: seed or reset admin ----
@app.cli.command("seed-admin")
@click.option("--email", default="admin@samprox.lk", help="Admin email")
@click.option("--password", default="Admin@123", help="Admin password")
def seed_admin(email, password):
    """Create or reset the admin user."""
    with app.app_context():
        status, normalized_email = _ensure_admin_user(
            email=email,
            password=password,
            ensure_if_missing=True,
            force_reset=True,
        )

        if status == "created":
            click.echo(f"✅ Admin created: {normalized_email}")
        elif status == "reset":
            click.echo(f"✅ Admin password reset: {normalized_email}")
        elif status == "updated":
            click.echo(f"✅ Admin role updated: {normalized_email}")
        else:
            click.echo(f"ℹ️ Admin already up-to-date: {normalized_email}")

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
