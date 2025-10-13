import os

import click
from flask import Flask, jsonify
from sqlalchemy import func

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


def _seed_admin_if_requested():
    """Create or reset the admin user when RUN_SEED_ADMIN=1 is set."""
    if os.getenv("RUN_SEED_ADMIN") != "1":
        return
    email = os.getenv("ADMIN_EMAIL", "admin@samprox.lk")
    password = os.getenv("ADMIN_PASSWORD", "Admin@123")

    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if not u:
            u = User(name="Admin", email=email, role=RoleEnum.admin)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            print(f"✅ Admin created: {email}")
        else:
            u.set_password(password)
            db.session.commit()
            print(f"✅ Admin password reset: {email}")


# Call the hook at startup (idempotent)
_seed_admin_if_requested()

# ---- CLI: seed or reset admin ----
@app.cli.command("seed-admin")
@click.option("--email", default="admin@samprox.lk", help="Admin email")
@click.option("--password", default="Admin@123", help="Admin password")
def seed_admin(email, password):
    """Create or reset the admin user."""
    with app.app_context():
        normalized_email = email.strip().lower()
        u = User.query.filter(func.lower(User.email) == normalized_email).first()
        if not u:
            u = User(name="Admin", email=normalized_email, role=RoleEnum.admin)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            click.echo(f"✅ Admin created: {normalized_email}")
        else:
            u.set_password(password)
            db.session.commit()
            click.echo(f"✅ Admin password reset: {normalized_email}")

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
