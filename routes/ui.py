import re
from datetime import date
from decimal import Decimal

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request
from sqlalchemy import func, tuple_

from material import (
    MaterialValidationError,
    get_mrn_detail,
    list_material_items,
)
from company_profiles import (
    available_company_keys,
    resolve_company_profile,
    select_company_key,
)
from schemas import MaterialItemSchema, MRNSchema

from extensions import db
from models import (
    Company,
    FinancialStatementLine,
    FinancialStatementValue,
    IFRS_TRIAL_BALANCE_CATEGORIES,
    RoleEnum,
    User,
    generate_financial_year_months,
)

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
        normalized_role = role.lower() if isinstance(role, str) else role
        return RoleEnum(normalized_role)
    except ValueError:
        return None


def _current_user() -> User | None:
    """Return the current user model if a JWT identity is present."""

    try:
        verify_jwt_in_request(optional=True)
    except Exception:  # pragma: no cover - defensive safety net
        return None

    identity = get_jwt_identity()
    if not identity:
        return None

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return None

    return User.query.get(user_id)


def _has_rainbows_end_market_access() -> bool:
    """Grant special market access to the Rainbows End Trading outside manager."""

    claims = None
    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt()
    except Exception:  # pragma: no cover - defensive safety net
        claims = None

    if claims:
        company_key = claims.get("company_key") or claims.get("company")
        try:
            role = RoleEnum(claims.get("role")) if claims.get("role") else None
        except ValueError:
            role = None
        if company_key == "rainbows-end-trading" and role == RoleEnum.outside_manager:
            return True

    user = _current_user()
    if not user:
        return False

    if user.company_key == "rainbows-end-trading" and user.role == RoleEnum.outside_manager:
        return True

    return user.email.lower() == "shamal@rainbowsholdings.com"


def _current_financial_year(today: date | None = None) -> int:
    reference = today or date.today()
    return reference.year if reference.month >= 4 else reference.year - 1


def _financial_year_options() -> list[int]:
    current_fy = _current_financial_year()
    start_year = 2023
    end_year = current_fy + 2
    return list(range(start_year, end_year + 1))


def _ensure_company_records() -> None:
    """Ensure configured company profiles exist as ``Company`` rows.

    The petty cash UI already relies on ``COMPANY_PROFILES``. When teams add new
    companies via configuration, mirror those entries into the ``companies``
    table so the financial statements dropdown stays in sync.
    """

    configured_keys = available_company_keys(current_app.config)
    if not configured_keys:
        return

    existing = {company.key: company for company in Company.query.all()}
    created = False

    for key in configured_keys:
        if key in existing:
            continue

        profile = resolve_company_profile(current_app.config, key)
        company = Company(key=profile.get("key"), name=profile.get("name") or key)
        db.session.add(company)
        created = True

    if created:
        db.session.commit()


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value or "")
    return cleaned.strip("_").lower()


def _load_financials_context(
    *,
    company_id: str | int | None = None,
    statement_type: str = "income",
    financial_year: int | None = None,
) -> dict[str, object]:
    allowed_statement_types = {"income", "sofp", "cashflow", "equity", "trial_balance"}
    statement = statement_type if statement_type in allowed_statement_types else "income"
    year = financial_year or _current_financial_year()

    _ensure_company_records()

    companies = Company.query.order_by(Company.name.asc()).all()
    selected_company_id: int | None = None
    is_group = False

    if str(company_id) == "group":
        is_group = True
    elif company_id:
        try:
            selected_company_id = int(company_id)
        except (TypeError, ValueError):
            selected_company_id = None
    elif companies:
        selected_company_id = companies[0].id

    months = generate_financial_year_months(year)
    lines = (
        FinancialStatementLine.query.filter_by(statement_type=statement)
        .order_by(FinancialStatementLine.display_order.asc())
        .all()
    )

    pairs = [(m["year"], m["month"]) for m in months]
    values_map: dict[tuple[int, int, str], Decimal] = {}

    if pairs and lines:
        month_filter = tuple_(FinancialStatementValue.year, FinancialStatementValue.month).in_(pairs)

        if is_group:
            query = (
                db.session.query(
                    FinancialStatementValue.year,
                    FinancialStatementValue.month,
                    FinancialStatementValue.line_key,
                    func.coalesce(func.sum(FinancialStatementValue.amount), 0),
                )
                .filter(FinancialStatementValue.statement_type == statement)
                .filter(month_filter)
                .group_by(
                    FinancialStatementValue.year,
                    FinancialStatementValue.month,
                    FinancialStatementValue.line_key,
                )
            )
            for year_value, month_value, line_key, amount in query:
                values_map[(int(year_value), int(month_value), line_key)] = Decimal(amount or 0)
        elif selected_company_id:
            records = (
                FinancialStatementValue.query.filter_by(
                    company_id=selected_company_id, statement_type=statement
                )
                .filter(month_filter)
                .all()
            )
            for record in records:
                values_map[(record.year, record.month, record.line_key)] = Decimal(record.amount or 0)

    return {
        "companies": companies,
        "selected_company_id": selected_company_id,
        "is_group": is_group,
        "statement_type": statement,
        "financial_year": year,
        "financial_year_options": _financial_year_options(),
        "financial_months": months,
        "financial_lines": lines,
        "financial_values_map": values_map,
        "is_trial_balance": statement == "trial_balance",
        "trial_balance_categories": IFRS_TRIAL_BALANCE_CATEGORIES,
    }


@bp.before_request
def _enforce_role_page_restrictions():
    """Limit which UI routes specific roles are allowed to access."""

    endpoint = request.endpoint
    if endpoint is None:
        return None

    role = _current_role()
    if role == RoleEnum.sales:
        allowed_endpoints = {"ui.login_page", "ui.money_page"}
        if endpoint in allowed_endpoints:
            return None
        return redirect(url_for("ui.money_page"))

    if role == RoleEnum.maintenance_manager:
        if endpoint in {"ui.machines_page", "ui.login_page"}:
            return None
        return redirect(url_for("ui.machines_page"))

    if role == RoleEnum.outside_manager:
        allowed_endpoints = {"ui.login_page", "ui.responsibility_portal"}
        if _has_rainbows_end_market_access():
            allowed_endpoints.update({"ui.market_page", "ui.market_rainbows_end_page"})
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
    return render_template("man.html")


@bp.get("/responsibility_portal")
def responsibility_portal():
    """Render the standalone responsibility planning portal."""

    role = _current_role()
    if role not in {
        RoleEnum.production_manager,
        RoleEnum.maintenance_manager,
        RoleEnum.finance_manager,
        RoleEnum.admin,
        RoleEnum.outside_manager,
    }:
        return render_template("403.html"), 403

    return render_template("responsibility_plan.html")


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

    company_key = request.args.get("company")

    claims = None
    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt()
    except Exception:  # pragma: no cover - defensive safety net for UI access
        claims = None

    selected_company_key = select_company_key(current_app.config, company_key, claims)
    company = resolve_company_profile(current_app.config, selected_company_key)

    return render_template("material/mrn_view.html", mrn=mrn, company=company)


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

    claims = None
    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt()
    except Exception:  # pragma: no cover - defensive safety net for UI access
        claims = None

    company_key = select_company_key(current_app.config, claims=claims)

    if company_key == "rainbows-end-trading":
        role = _current_role()
        if role != RoleEnum.admin and not _has_rainbows_end_market_access():
            return render_template("403.html"), 403

        company = resolve_company_profile(current_app.config, company_key)
        return render_template("market_rainbows_end.html", company=company)

    return render_template("market.html")


@bp.get("/market_rainbows_end.html")
@bp.get("/market_rainbows_end")
def market_rainbows_end_page():
    """Render the Rainbows End Trading admin market page directly."""

    claims = None
    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt()
    except Exception:  # pragma: no cover - defensive safety net for UI access
        claims = None

    company_key = select_company_key(current_app.config, claims=claims)
    if company_key != "rainbows-end-trading":
        return redirect(url_for("ui.market_page"))

    role = _current_role()
    if role != RoleEnum.admin and not _has_rainbows_end_market_access():
        return render_template("403.html"), 403

    company = resolve_company_profile(current_app.config, company_key)
    return render_template("market_rainbows_end.html", company=company)


@bp.get("/movers")
def movers_page():
    """Render the logistics and movers overview page."""

    return render_template("movers.html")


@bp.get("/money")
def money_page():
    """Render the financial overview page."""
    context = _load_financials_context()
    is_sales = _current_role() == RoleEnum.sales
    active_tab = "petty-cash" if is_sales else "overview"
    context.update({"active_tab": active_tab, "is_sales": is_sales})
    return render_template("money.html", **context)


@bp.get("/money/financials")
def financials_page():
    """Render the manual financials capture UI."""

    if _current_role() == RoleEnum.sales:
        return render_template("403.html"), 403

    company_id = request.args.get("company_id")
    statement_type = request.args.get("statement_type", "income")
    financial_year = request.args.get("financial_year")
    try:
        parsed_year = int(financial_year) if financial_year else None
    except (TypeError, ValueError):
        parsed_year = None

    context = _load_financials_context(
        company_id=company_id, statement_type=statement_type, financial_year=parsed_year
    )
    context.update({"active_tab": "financials", "is_sales": False})
    return render_template("money.html", **context)


@bp.post("/money/financials/save")
def save_financials():
    if _current_role() == RoleEnum.sales:
        return render_template("403.html"), 403

    statement_type = request.form.get("statement_type", "income")
    financial_year_raw = request.form.get("financial_year")
    company_id_raw = request.form.get("company_id")

    try:
        financial_year = int(financial_year_raw) if financial_year_raw else _current_financial_year()
    except (TypeError, ValueError):
        financial_year = _current_financial_year()

    if company_id_raw == "group" or not company_id_raw:
        flash("Select a specific company to save financials.", "error")
        return redirect(
            url_for(
                "ui.financials_page",
                company_id=company_id_raw,
                statement_type=statement_type,
                financial_year=financial_year,
            )
        )

    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        flash("Invalid company selection.", "error")
        return redirect(
            url_for(
                "ui.financials_page",
                company_id=company_id_raw,
                statement_type=statement_type,
                financial_year=financial_year,
            )
        )

    company = Company.query.get(company_id)
    if not company:
        flash("Selected company was not found.", "error")
        return redirect(
            url_for(
                "ui.financials_page",
                company_id=company_id_raw,
                statement_type=statement_type,
                financial_year=financial_year,
            )
        )

    allowed_statement_types = {"income", "sofp", "cashflow", "equity", "trial_balance"}
    statement = statement_type if statement_type in allowed_statement_types else "income"
    allowed_pairs = {(m["year"], m["month"]) for m in generate_financial_year_months(financial_year)}

    updates_made = 0
    for key, raw_value in request.form.items():
        match = re.match(r"values\[(\d{4})\]\[(\d{1,2})\]\[(.+)\]", key)
        if not match:
            continue
        year, month, line_key = match.groups()
        try:
            year_int = int(year)
            month_int = int(month)
        except ValueError:
            continue

        if (year_int, month_int) not in allowed_pairs:
            continue

        try:
            amount = Decimal(raw_value or "0")
        except Exception:
            amount = Decimal("0")

        existing = FinancialStatementValue.query.filter_by(
            company_id=company.id,
            year=year_int,
            month=month_int,
            statement_type=statement,
            line_key=line_key,
        ).first()

        if existing:
            if existing.amount != amount:
                existing.amount = amount
                updates_made += 1
        else:
            db.session.add(
                FinancialStatementValue(
                    company_id=company.id,
                    year=year_int,
                    month=month_int,
                    statement_type=statement,
                    line_key=line_key,
                    amount=amount,
                )
            )
            updates_made += 1

    if updates_made:
        db.session.commit()
        flash("Financial data saved successfully.", "success")
    else:
        flash("No changes to save.", "info")

    return redirect(
        url_for(
            "ui.financials_page",
            company_id=company.id,
            statement_type=statement,
            financial_year=financial_year,
        )
    )


@bp.post("/money/financials/new-line")
def create_financial_line():
    if _current_role() == RoleEnum.sales:
        return render_template("403.html"), 403

    statement_type = request.form.get("statement_type", "income")
    label = (request.form.get("label") or "").strip()
    line_key_input = (request.form.get("line_key") or "").strip()
    level_raw = request.form.get("level")
    display_order_raw = request.form.get("display_order")
    is_section = bool(request.form.get("is_section"))
    is_subtotal = bool(request.form.get("is_subtotal"))

    allowed_statement_types = {"income", "sofp", "cashflow", "equity", "trial_balance"}
    if statement_type not in allowed_statement_types:
        statement_type = "income"

    try:
        level = int(level_raw) if level_raw is not None else 0
    except (TypeError, ValueError):
        level = 0

    existing_max = (
        db.session.query(func.max(FinancialStatementLine.display_order))
        .filter(FinancialStatementLine.statement_type == statement_type)
        .scalar()
    )
    default_order = (existing_max or 0) + 10

    try:
        display_order = int(display_order_raw) if display_order_raw else default_order
    except (TypeError, ValueError):
        display_order = default_order

    if not label:
        flash("Label is required to create a new line.", "error")
        return redirect(url_for("ui.financials_page", statement_type=statement_type))

    line_key = line_key_input or _slugify(label) or f"line_{display_order}"

    exists = FinancialStatementLine.query.filter_by(
        statement_type=statement_type, line_key=line_key
    ).first()
    if exists:
        flash("A line with this key already exists for the statement.", "error")
        return redirect(url_for("ui.financials_page", statement_type=statement_type))

    db.session.add(
        FinancialStatementLine(
            statement_type=statement_type,
            line_key=line_key,
            label=label,
            display_order=display_order,
            level=level,
            is_section=is_section,
            is_subtotal=is_subtotal,
            is_calculated=False,
        )
    )
    db.session.commit()
    flash("New statement line created.", "success")

    return redirect(
        url_for(
            "ui.financials_page",
            statement_type=statement_type,
            company_id=request.form.get("company_id"),
            financial_year=request.form.get("financial_year"),
        )
    )


@bp.get("/manufacturing")
def manufacturing_page():
    """Render the manufacturing operations page."""
    return render_template("manufacturing.html")


@bp.get("/mechanism")
def mechanism_page():
    """Render the user role management page."""
    return render_template("mechanism.html")
