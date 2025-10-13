from flask import Flask, jsonify
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from config import Config
from extensions import db, migrate, jwt
from routes import auth, jobs, quotation, labor, materials, reports, ui
from models import User, RoleEnum
import os

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

    def ensure_default_admin():
        """Ensure there is at least one administrator account available."""
        try:
            inspector = inspect(db.engine)
        except SQLAlchemyError:
            return

        if not inspector.has_table(User.__tablename__):
            return

        email = app.config["DEFAULT_ADMIN_EMAIL"]
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(
                name=app.config["DEFAULT_ADMIN_NAME"],
                email=email,
                role=RoleEnum.admin,
            )
            user.set_password(app.config["DEFAULT_ADMIN_PASSWORD"])
            db.session.add(user)
            db.session.commit()
        else:
            updated = False
            if user.role != RoleEnum.admin:
                user.role = RoleEnum.admin
                updated = True
            if not user.active:
                user.active = True
                updated = True
            if updated:
                db.session.commit()

    with app.app_context():
        ensure_default_admin()

    @app.get("/api/health")
    def health(): return jsonify({"ok": True})

    return app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
