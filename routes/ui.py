from flask import Blueprint, render_template

bp = Blueprint("ui", __name__)


@bp.get("/")
def login_page():
    """Render the landing login page."""
    return render_template("login.html")
