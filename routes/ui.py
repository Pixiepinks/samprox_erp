from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_jwt_extended import get_jwt, verify_jwt_in_request

from material import (
    MaterialValidationError,
    get_mrn_detail,
    list_material_items,
)
from schemas import MaterialItemSchema, MRNSchema
from responsibility_metrics import list_unit_options

from models import RoleEnum

bp = Blueprint("ui", __name__)


def _current_role() -> RoleEnum | None:
    """Return the current user's role if a JWT is provided."""

    try:
        verify_jwt_in_request(optional=True)
    except Exception:  # pragma: no cover - defensive safety net
        return None

    try:
        claims = get_jwt()
    except RuntimeError:
        return None

    role = claims.get("role") if claims else None
    if not role:
        return None

    try:
        return RoleEnum(role)
    except ValueError:
        return None


@bp.before_request
def _enforce_role_page_restrictions():
    """Limit which UI routes specific roles are allowed to access."""

    endpoint = request.endpoint
    if endpoint is None:
        return None

    role = _current_role()
    if role == RoleEnum.maintenance_manager:
        if endpoint in {"ui.machines_page", "ui.login_page"}:
            return None
        return redirect(url_for("ui.machines_page"))

    if role == RoleEnum.outside_manager:
        allowed_endpoints = {"ui.login_page", "ui.responsibility_portal"}
        if endpoint in allowed_endpoints:
            return None
        return render_template("403.html"), 403


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
    return render_template("man.html", perf_unit_options=list_unit_options())


@bp.get("/responsibility_portal")
def responsibility_portal():
    """Render the standalone responsibility planning portal."""

    role = _current_role()
    if role not in {RoleEnum.outside_manager}:
        return render_template("403.html"), 403

    return render_template(
        "responsibility_plan.html",
        perf_unit_options=list_unit_options(),
    )


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
    return render_template(
        "material/mrn_new.html",
        items=item_options,
        is_edit_mode=False,
    )


@bp.get("/material/mrn/<mrn_id>")
def material_mrn_view_page(mrn_id: str):
    """Render a read-only MRN summary page."""
    try:
        mrn = get_mrn_detail(mrn_id)
    except MaterialValidationError:
        abort(404)
    return render_template("material/mrn_view.html", mrn=mrn)


@bp.get("/material/mrn/<mrn_id>/edit")
def material_mrn_edit_page(mrn_id: str):
    """Render the Material Receipt Note form with existing data for editing."""
    try:
        mrn = get_mrn_detail(mrn_id)
    except MaterialValidationError:
        abort(404)

    items = list_material_items()
    item_schema = MaterialItemSchema(many=True)
    item_options = item_schema.dump(items)
    mrn_payload = MRNSchema().dump(mrn)

    return render_template(
        "material/mrn_new.html",
        items=item_options,
        mrn=mrn_payload,
        is_edit_mode=True,
    )


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


@bp.get("/mechanism")
def mechanism_page():
    """Render the user role management page."""
    return render_template("mechanism.html")
