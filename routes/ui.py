from flask import Blueprint, abort, redirect, render_template, url_for

from material import MaterialValidationError, get_mrn_detail, list_material_items
from schemas import MaterialItemSchema

bp = Blueprint("ui", __name__)


@bp.get("/")
def login_page():
    """Render the landing login page."""
    return render_template("login.html")


@bp.get("/jobs")
def jobs_page():
    """Redirect legacy jobs route to the machines page."""
    return redirect(url_for("ui.machines_page"))


@bp.get("/mind")
def mind_page():
    """Render the Mind operations overview."""
    return render_template("mind.html")


@bp.get("/dashboard")
def dashboard_redirect():
    """Preserve the legacy dashboard route by redirecting to Mind."""
    return redirect(url_for("ui.mind_page"))


@bp.get("/man")
def man_page():
    """Render the "Man" resource planning page."""
    return render_template("man.html")


@bp.get("/machines")
def machines_page():
    """Render the machine operations hub."""
    return render_template("machines.html")


@bp.get("/material")
def material_page():
    """Render the material tracking page."""
    return render_template("material.html")


@bp.get("/material/mrn/new")
def material_mrn_new_page():
    """Render the Material Receipt Note capture form."""
    items = list_material_items()
    item_schema = MaterialItemSchema(many=True)
    item_options = item_schema.dump(items)
    return render_template("material/mrn_new.html", items=item_options)


@bp.get("/material/mrn/<mrn_id>")
def material_mrn_view_page(mrn_id: str):
    """Render a read-only MRN summary page."""
    try:
        mrn = get_mrn_detail(mrn_id)
    except MaterialValidationError:
        abort(404)
    return render_template("material/mrn_view.html", mrn=mrn)


@bp.get("/market")
def market_page():
    """Render the market analysis page."""
    return render_template("market.html")


@bp.get("/money")
def money_page():
    """Render the financial overview page."""
    return render_template("money.html")


@bp.get("/manufacturing")
def manufacturing_page():
    """Render the manufacturing operations page."""
    return render_template("manufacturing.html")
