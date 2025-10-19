import os
from typing import Optional, Tuple

import click
from flask import Flask, jsonify
from sqlalchemy import create_engine, func, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.engine import make_url

from config import Config, current_database_url
from extensions import db, migrate, jwt
from models import (
    Customer,
    CustomerCategory,
    CustomerCreditTerm,
    CustomerTransportMode,
    CustomerType,
    RoleEnum,
    SalesActualEntry,
    SalesForecastEntry,
    User,
)
from routes import auth, jobs, quotation, labor, materials, machines, market, production, reports, team, ui


def _ensure_database_exists(database_url: str | None) -> None:
    if not database_url:
        return

    url = make_url(database_url)
    backend = (url.get_backend_name() or "").lower()

    if backend.startswith("sqlite"):
        database_path = url.database
        if database_path and database_path not in {":memory:", ""}:
            directory = os.path.dirname(os.path.abspath(database_path))
            if directory:
                os.makedirs(directory, exist_ok=True)
        return

    database_name = url.database
    if not database_name:
        return

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return
    except OperationalError:
        pass
    finally:
        engine.dispose()

    if not backend.startswith("postgresql"):
        return

    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database_name},
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    database_url = current_database_url()
    _ensure_database_exists(database_url)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
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
    app.register_blueprint(machines.bp)
    app.register_blueprint(production.bp)
    app.register_blueprint(market.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(team.bp)
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

    target_app = flask_app or globals().get("app")
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


def _ensure_accessall_user(
    flask_app=None,
    *,
    email: Optional[str] = None,
    password: Optional[str] = None,
    name: Optional[str] = None,
    ensure_if_missing: bool = True,
    force_reset: bool = False,
):
    target_app = flask_app or globals().get("app")
    normalized_email = _normalize_email(email or os.getenv("ACCESSALL_EMAIL", "accessall@samprox.lk"))
    password = password or os.getenv("ACCESSALL_PASSWORD", "123")
    name = name or os.getenv("ACCESSALL_NAME", "Accessall")

    if target_app is None:
        return "skipped", normalized_email

    with target_app.app_context():
        try:
            user = User.query.filter(func.lower(User.email) == normalized_email).first()
        except (OperationalError, ProgrammingError):
            return "skipped", normalized_email

        if user:
            status = "skipped"
            updated = False

            if user.name != name:
                user.name = name
                updated = True
            if user.role != RoleEnum.production_manager:
                user.role = RoleEnum.production_manager
                updated = True
            if not user.active:
                user.active = True
                updated = True

            if force_reset:
                user.set_password(password)
                status = "reset"
            elif updated:
                status = "updated"

            if status != "skipped":
                db.session.commit()

            return status, normalized_email

        if not ensure_if_missing:
            return "skipped", normalized_email

        user = User(
            name=name,
            email=normalized_email,
            role=RoleEnum.production_manager,
            active=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return "created", normalized_email


def _bootstrap_admin_user(flask_app=None):
    status, normalized_email = _ensure_admin_user(
        flask_app=flask_app,
        force_reset=os.getenv("RUN_SEED_ADMIN") == "1",
    )
    if status == "created":
        print(f"✅ Admin created: {normalized_email}")
    elif status == "reset":
        print(f"✅ Admin password reset: {normalized_email}")
    elif status == "updated":
        print(f"✅ Admin role updated: {normalized_email}")


def _bootstrap_accessall_user(flask_app=None):
    status, normalized_email = _ensure_accessall_user(
        flask_app=flask_app,
        force_reset=os.getenv("RUN_SEED_ACCESSALL") == "1",
    )
    if status == "created":
        print(f"✅ Accessall user created: {normalized_email}")
    elif status == "reset":
        print(f"✅ Accessall password reset: {normalized_email}")
    elif status == "updated":
        print(f"✅ Accessall user updated: {normalized_email}")
# Call the hooks at startup (idempotent)
_bootstrap_admin_user(flask_app=app)
_bootstrap_accessall_user(flask_app=app)

# ---- CLI: seed or reset admin ----
@app.cli.command("seed-admin")
@click.option("--email", default="admin@samprox.lk", help="Admin email")
@click.option("--password", default="Admin@123", help="Admin password")
def seed_admin(email, password):
    """Create or reset the admin user."""
    with app.app_context():
        status, normalized_email = _ensure_admin_user(
            flask_app=app,
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


@app.cli.command("seed-accessall")
@click.option("--email", default="accessall@samprox.lk", help="Accessall email")
@click.option("--password", default="123", help="Accessall password")
@click.option("--name", default="Accessall", help="Accessall display name")
def seed_accessall(email, password, name):
    """Create or reset the Accessall user."""

    with app.app_context():
        status, normalized_email = _ensure_accessall_user(
            flask_app=app,
            email=email,
            password=password,
            name=name,
            ensure_if_missing=True,
            force_reset=True,
        )

        if status == "created":
            click.echo(f"✅ Accessall user created: {normalized_email}")
        elif status == "reset":
            click.echo(f"✅ Accessall password reset: {normalized_email}")
        elif status == "updated":
            click.echo(f"✅ Accessall user updated: {normalized_email}")
        else:
            click.echo(f"ℹ️ Accessall user already up-to-date: {normalized_email}")

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
