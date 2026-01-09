import calendar
import os
from datetime import date as dt_date, datetime, time as dt_time, timedelta
from typing import Optional, Tuple

import click
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from flask import Flask, jsonify, request
from flask_jwt_extended import get_jwt, verify_jwt_in_request
from sqlalchemy import create_engine, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError

from config import Config, current_database_url
from extensions import db, migrate, jwt, mail
from exsol_storage import init_exsol_storage
from models import (
    Company,
    Customer,
    CustomerCategory,
    CustomerCreditTerm,
    CustomerTransportMode,
    CustomerType,
    FinancialStatementLine,
    FinancialStatementValue,
    FinancialTrialBalanceLine,
    ChartOfAccount,
    MaterialItem,
    MachineAsset,
    MachineIdleEvent,
    MRNHeader,
    MRNLine,
    RoleEnum,
    SALES_MANAGER_ROLES,
    Supplier,
    SalesActualEntry,
    SalesForecastEntry,
    ProductionForecastEntry,
    ResponsibilityTask,
    User,
    TeamLeaveBalance,
    TeamMember,
    TeamMemberStatus,
    PayCategory,
    TeamWorkCalendarDay,
    normalize_role,
)
from routes import (
    auth,
    jobs,
    maintenance_jobs,
    maintenance_job_documents,
    quotation,
    labor,
    materials,
    machines,
    market,
    customer_pos,
    material_api,
    production,
    petty_cash,
    responsibilities,
    chart_of_accounts,
    system,
    reports,
    team,
    ui,
    users,
    financials,
    companies,
    non_samprox_customers,
    sales_visits,
    dealers,
    sales_users,
    exsol_inventory,
    exsol_production,
    exsol_sales,
)


if os.name != "nt":  # pragma: no cover - platform dependent import
    import fcntl  # type: ignore[import-not-found]
else:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


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


def _run_database_migrations(app: Flask) -> None:
    """Apply Alembic migrations if the schema is not up-to-date."""

    database_uri = app.config.get("SQLALCHEMY_DATABASE_URI")
    if not database_uri:
        return

    if database_uri.startswith("sqlite") and ":memory:" in database_uri:
        return

    migrations_dir = os.path.join(app.root_path, "migrations")
    alembic_ini = os.path.join(migrations_dir, "alembic.ini")
    if not os.path.exists(alembic_ini):
        return

    config = AlembicConfig(alembic_ini)
    config.set_main_option("script_location", migrations_dir)
    config.set_main_option("sqlalchemy.url", database_uri)

    script = ScriptDirectory.from_config(config)
    head_revision = script.get_current_head()
    if not head_revision:
        return

    def _current_revision() -> str | None:
        try:
            with db.engine.connect() as connection:
                return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except (OperationalError, ProgrammingError):
            return None

    with app.app_context():
        if _current_revision() == head_revision:
            return

        lock_path = os.path.join(app.instance_path, "alembic.lock")
        os.makedirs(app.instance_path, exist_ok=True)
        lock_file = open(lock_path, "w")
        try:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_EX)

            if _current_revision() == head_revision:
                return

            app.logger.info("Applying database migrations…")
            try:
                command.upgrade(config, "head")
            except Exception:
                if _current_revision() != head_revision:
                    raise
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    database_url = current_database_url()
    _ensure_database_exists(database_url)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_SESSION_OPTIONS"] = {"expire_on_commit": False}
    db.init_app(app)
    migrate.init_app(app, db)
    _run_database_migrations(app)
    jwt.init_app(app)
    mail.init_app(app)
    init_exsol_storage(app)

    @jwt.additional_claims_loader
    def add_claims(identity):
        u = User.query.get(identity)
        return {"role": u.role if u else None}

    app.register_blueprint(auth.bp)
    app.register_blueprint(jobs.bp)
    app.register_blueprint(maintenance_jobs.bp)
    app.register_blueprint(maintenance_job_documents.bp)
    app.register_blueprint(quotation.bp)
    app.register_blueprint(labor.bp)
    app.register_blueprint(materials.bp)
    app.register_blueprint(material_api.bp)
    app.register_blueprint(chart_of_accounts.bp)
    app.register_blueprint(financials.bp)
    app.register_blueprint(machines.bp)
    app.register_blueprint(customer_pos.bp)
    app.register_blueprint(production.bp)
    app.register_blueprint(petty_cash.bp)
    app.register_blueprint(responsibilities.bp)
    app.register_blueprint(system.bp)
    app.register_blueprint(market.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(team.bp)
    app.register_blueprint(ui.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(companies.bp)
    app.register_blueprint(non_samprox_customers.bp)
    app.register_blueprint(sales_visits.bp)
    app.register_blueprint(dealers.bp)
    app.register_blueprint(sales_users.bp)
    app.register_blueprint(exsol_inventory.bp)
    app.register_blueprint(exsol_production.bp)
    app.register_blueprint(exsol_sales.bp)

    def _jwt_role() -> RoleEnum | None:
        try:
            verify_jwt_in_request(optional=True)
        except Exception:
            return None

        try:
            claims = get_jwt()
        except Exception:
            claims = None

        if not claims:
            return None

        return normalize_role(claims.get("role"))

    @app.before_request
    def _limit_sales_manager_scope():
        role = _jwt_role()
        if role not in SALES_MANAGER_ROLES:
            return None

        endpoint = request.endpoint or ""
        if not endpoint or endpoint.startswith("static"):
            return None

        allowed_endpoints = {
            "auth.login",
            "auth.logout",
            "auth.session",
            "ui.login_page",
            "ui.sales_dashboard_page",
            "ui.sales_data_entry_page",
            "ui.sales_reports_page",
            "ui.sales_production_page",
            "ui.sales_invoice_page",
            "ui.sales_visits_page",
            "ui.sales_visits_alias",
            "ui.sales_dashboard_redirect",
            "ui.exsol_inventory_page",
            "health",
        }
        allowed_report_endpoints = {
            "reports.customer_sales_report",
            "reports.sales_summary",
            "reports.monthly_sales_summary",
        }

        if endpoint in allowed_endpoints:
            return None
        allowed_prefixes = ("sales_visits.", "non_samprox_customers.", "sales_users.", "market.", "companies_api.")
        if endpoint.startswith(allowed_prefixes):
            return None
        allowed_paths = (
            "/api/sales-visits",
            "/api/non-samprox-customers",
            "/api/sales-users",
            "/api/market",
            "/api/companies",
            "/api/exsol",
        )
        if request.path.startswith(allowed_paths):
            return None
        if endpoint in allowed_report_endpoints:
            return None

        return jsonify({"ok": False, "error": "Access denied"}), 403

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
    name: Optional[str] = None,
    ensure_if_missing: bool = True,
    force_reset: bool = False,
    allow_multiple: bool = False,
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
    provided_name = name if name is not None else os.getenv("ADMIN_NAME")
    target_name = (provided_name or "").strip() or None

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
            if target_name and admin.name != target_name:
                admin.name = target_name
                status = "updated"
            if force_reset:
                admin.set_password(password)
                status = "reset"

            if status != "skipped":
                db.session.commit()
            return status, normalized_email

        if not ensure_if_missing:
            return "skipped", normalized_email

        if not force_reset and not allow_multiple:
            # Avoid creating duplicate admins when one already exists
            existing_admin = User.query.filter_by(role=RoleEnum.admin).first()
            if existing_admin:
                return "skipped", normalized_email

        admin = User(
            name=target_name or "Admin",
            email=normalized_email,
            role=RoleEnum.admin,
            active=True,
        )
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
    normalized_email = _normalize_email(email or os.getenv("ACCESSALL_EMAIL", "production@samprox.lk"))
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


def _ensure_maintenance_user(
    flask_app=None,
    *,
    email: Optional[str] = None,
    password: Optional[str] = None,
    name: Optional[str] = None,
    ensure_if_missing: bool = True,
    force_reset: bool = False,
):
    target_app = flask_app or globals().get("app")
    normalized_email = _normalize_email(email or os.getenv("MAINTENANCE_EMAIL", "maintenance@samprox.lk"))
    password = password or os.getenv("MAINTENANCE_PASSWORD", "123")
    name = name or os.getenv("MAINTENANCE_NAME", "Maintenance")

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
            if user.role != RoleEnum.maintenance_manager:
                user.role = RoleEnum.maintenance_manager
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
            role=RoleEnum.maintenance_manager,
            active=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return "created", normalized_email


def _ensure_finance_user(
    flask_app=None,
    *,
    email: Optional[str] = None,
    password: Optional[str] = None,
    name: Optional[str] = None,
    ensure_if_missing: bool = True,
    force_reset: bool = False,
):
    target_app = flask_app or globals().get("app")
    normalized_email = _normalize_email(email or os.getenv("FINANCE_EMAIL", "finance@samprox.lk"))
    password = password or os.getenv("FINANCE_PASSWORD", "123")
    name = name or os.getenv("FINANCE_NAME", "Finance Manager")

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
            if user.role != RoleEnum.finance_manager:
                user.role = RoleEnum.finance_manager
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
            role=RoleEnum.finance_manager,
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


def _bootstrap_maintenance_user(flask_app=None):
    status, normalized_email = _ensure_maintenance_user(
        flask_app=flask_app,
        force_reset=os.getenv("RUN_SEED_MAINTENANCE") == "1",
    )
    if status == "created":
        print(f"✅ Maintenance user created: {normalized_email}")
    elif status == "reset":
        print(f"✅ Maintenance password reset: {normalized_email}")
    elif status == "updated":
        print(f"✅ Maintenance user updated: {normalized_email}")


def _bootstrap_finance_user(flask_app=None):
    status, normalized_email = _ensure_finance_user(
        flask_app=flask_app,
        force_reset=os.getenv("RUN_SEED_FINANCE") == "1",
    )
    if status == "created":
        print(f"✅ Finance user created: {normalized_email}")
    elif status == "reset":
        print(f"✅ Finance password reset: {normalized_email}")
    elif status == "updated":
        print(f"✅ Finance user updated: {normalized_email}")


def _bootstrap_rainbows_admin_user(flask_app=None):
    status, normalized_email = _ensure_admin_user(
        flask_app=flask_app,
        email=os.getenv("RAINBOWS_ADMIN_EMAIL", "uresha@rainbowsholdings.com"),
        password=os.getenv("RAINBOWS_ADMIN_PASSWORD", "123"),
        name=os.getenv("RAINBOWS_ADMIN_NAME", "Uresha"),
        allow_multiple=True,
        force_reset=os.getenv("RUN_SEED_RAINBOWS_ADMIN") == "1",
    )
    if status == "created":
        print(f"✅ Rainbow admin created: {normalized_email}")
    elif status == "reset":
        print(f"✅ Rainbow admin password reset: {normalized_email}")
    elif status == "updated":
        print(f"✅ Rainbow admin updated: {normalized_email}")


# Call the hooks at startup (idempotent)
_bootstrap_admin_user(flask_app=app)
_bootstrap_accessall_user(flask_app=app)
_bootstrap_maintenance_user(flask_app=app)
_bootstrap_finance_user(flask_app=app)
_bootstrap_rainbows_admin_user(flask_app=app)


def _ensure_asset(code: str, *, name: str, location: str, status: str) -> MachineAsset:
    asset = MachineAsset.query.filter(func.lower(MachineAsset.code) == code.lower()).first()
    if asset is None:
        asset = MachineAsset(code=code, name=name, location=location, status=status)
        db.session.add(asset)
    else:
        updated = False
        for field, value in {
            "name": name,
            "location": location,
            "status": status,
        }.items():
            if getattr(asset, field) != value:
                setattr(asset, field, value)
                updated = True
        if updated:
            db.session.add(asset)
    return asset


# ---- CLI: seed idle monitoring demo ----
@app.cli.command("seed-idle-demo")
@click.option("--period", help="Target month in YYYY-MM format (defaults to current month)")
def seed_idle_demo(period):
    """Populate demo assets and idle events for the idle monitoring dashboard."""

    def _month_end(day: dt_date) -> dt_date:
        last_day = calendar.monthrange(day.year, day.month)[1]
        return dt_date(day.year, day.month, last_day)

    with app.app_context():
        if period:
            try:
                anchor = datetime.strptime(f"{period}-01", "%Y-%m-%d").date()
            except ValueError as exc:  # pragma: no cover - CLI validation
                raise click.BadParameter("Period must use YYYY-MM format.") from exc
        else:
            anchor = dt_date.today().replace(day=1)

        month_start = anchor
        month_end = _month_end(anchor)
        today = dt_date.today()
        target_day = min(max(today, month_start), month_end)

        assets = {
            "MCH-0001": _ensure_asset(
                "MCH-0001",
                name="Cutting Cell",
                location="Fabrication Hall",
                status="Running",
            ),
            "MCH-0002": _ensure_asset(
                "MCH-0002",
                name="Precision Lathe",
                location="Machining Bay",
                status="Running",
            ),
            "MCH-0003": _ensure_asset(
                "MCH-0003",
                name="Finishing Line",
                location="Assembly Floor",
                status="Running",
            ),
        }

        db.session.flush()

        lower_codes = [code.lower() for code in assets]
        start_bound = datetime.combine(month_start, dt_time.min)
        end_bound = datetime.combine(month_end, dt_time.max)

        events_to_clear = (
            MachineIdleEvent.query.join(MachineAsset)
            .filter(func.lower(MachineAsset.code).in_(lower_codes))
            .filter(MachineIdleEvent.started_at >= start_bound)
            .filter(MachineIdleEvent.started_at <= end_bound)
            .all()
        )

        for event in events_to_clear:
            db.session.delete(event)

        sample_events = [
            {
                "code": "MCH-0001",
                "start": datetime.combine(target_day, dt_time(7, 45)),
                "end": datetime.combine(target_day, dt_time(9, 5)),
                "reason": "Machine",
                "secondary": "Feeder Box Issue",
                "notes": "Operator swapped the worn blade and re-ran calibration.",
            },
            {
                "code": "MCH-0001",
                "start": datetime.combine(target_day, dt_time(16, 20)),
                "end": datetime.combine(target_day, dt_time(17, 0)),
                "reason": "Other",
                "secondary": "Changeover setup",
                "notes": "Setup for the evening order batch.",
            },
            {
                "code": "MCH-0002",
                "start": datetime.combine(target_day, dt_time(10, 15)),
                "end": datetime.combine(target_day, dt_time(11, 0)),
                "reason": "Material",
                "secondary": "Material Sourcing Issue",
                "notes": "Awaited feedstock delivery from stores.",
            },
            {
                "code": "MCH-0003",
                "start": datetime.combine(target_day, dt_time(13, 30)),
                "end": datetime.combine(target_day, dt_time(14, 10)),
                "reason": "Other",
                "secondary": "Planned maintenance",
                "notes": "Lubrication check on conveyor bearings.",
            },
        ]

        previous_day = target_day - timedelta(days=1)
        if previous_day >= month_start:
            sample_events.extend(
                [
                    {
                        "code": "MCH-0002",
                        "start": datetime.combine(previous_day, dt_time(18, 30)),
                        "end": datetime.combine(previous_day, dt_time(19, 45)),
                        "reason": "Machine",
                        "secondary": "Oil Circulation Issue",
                        "notes": "Replaced coolant pump before late shift.",
                    },
                    {
                        "code": "MCH-0003",
                        "start": datetime.combine(previous_day, dt_time(8, 0)),
                        "end": datetime.combine(previous_day, dt_time(8, 40)),
                        "reason": "Labor",
                        "secondary": "Key Member Absent",
                        "notes": "Operators assisted with urgent dispatch.",
                    },
                ]
            )

        for event in sample_events:
            code = event.get("code")
            start = event.get("start")
            end = event.get("end")
            reason = event.get("reason")
            notes = event.get("notes")
            secondary = event.get("secondary")

            asset = assets.get(code)
            if not asset:
                continue
            db.session.add(
                MachineIdleEvent(
                    asset_id=asset.id,
                    started_at=start,
                    ended_at=end,
                    reason=reason,
                    secondary_reason=secondary,
                    notes=notes,
                )
            )

        db.session.commit()
        click.echo(
            f"✅ Seeded idle demo data for {len(sample_events)} events between {month_start} and {month_end}."
        )


# ---- CLI: seed or reset admin ----
@app.cli.command("seed-admin")
@click.option("--email", default="admin@samprox.lk", help="Admin email")
@click.option("--password", default="Admin@123", help="Admin password")
@click.option("--name", default="Admin", help="Admin display name")
def seed_admin(email, password, name):
    """Create or reset the admin user."""
    with app.app_context():
        status, normalized_email = _ensure_admin_user(
            flask_app=app,
            email=email,
            password=password,
            name=name,
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
@click.option("--email", default="production@samprox.lk", help="Production manager email")
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


@app.cli.command("seed-finance")
@click.option("--email", default="finance@samprox.lk", help="Finance manager email")
@click.option("--password", default="123", help="Finance manager password")
@click.option("--name", default="Finance Manager", help="Finance manager display name")
def seed_finance(email, password, name):
    """Create or reset the finance manager user."""

    with app.app_context():
        status, normalized_email = _ensure_finance_user(
            flask_app=app,
            email=email,
            password=password,
            name=name,
            ensure_if_missing=True,
            force_reset=True,
        )

        if status == "created":
            click.echo(f"✅ Finance user created: {normalized_email}")
        elif status == "reset":
            click.echo(f"✅ Finance password reset: {normalized_email}")
        elif status == "updated":
            click.echo(f"✅ Finance user updated: {normalized_email}")
        else:
            click.echo(f"ℹ️ Finance user already up-to-date: {normalized_email}")


@app.cli.command("seed-rainbows-admin")
@click.option("--email", default="uresha@rainbowsholdings.com", help="Rainbow admin email")
@click.option("--password", default="123", help="Rainbow admin password")
@click.option("--name", default="Uresha", help="Rainbow admin display name")
def seed_rainbows_admin(email, password, name):
    """Create or reset the Rainbow Holdings admin user."""

    with app.app_context():
        status, normalized_email = _ensure_admin_user(
            flask_app=app,
            email=email,
            password=password,
            name=name,
            ensure_if_missing=True,
            force_reset=True,
            allow_multiple=True,
        )

        if status == "created":
            click.echo(f"✅ Rainbow admin created: {normalized_email}")
        elif status == "reset":
            click.echo(f"✅ Rainbow admin password reset: {normalized_email}")
        elif status == "updated":
            click.echo(f"✅ Rainbow admin updated: {normalized_email}")
        else:
            click.echo(f"ℹ️ Rainbow admin already up-to-date: {normalized_email}")


@app.cli.command("seed-materials")
def seed_materials() -> None:
    """Seed material categories and their default types."""

    from material import seed_material_defaults

    with app.app_context():
        seed_material_defaults()
        click.echo("✅ Material categories and default types seeded.")


@app.cli.command("seed-exsol-inventory")
def seed_exsol_inventory() -> None:
    """Seed the Exsol inventory catalog into the isolated storage."""

    from exsol_inventory import seed_exsol_defaults
    from exsol_storage import ExsolStorageUnavailable

    with app.app_context():
        try:
            count = seed_exsol_defaults()
        except ExsolStorageUnavailable as exc:
            click.echo(f"❌ {exc}")
            return
        click.echo(f"✅ Seeded {count} Exsol stock items.")


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
