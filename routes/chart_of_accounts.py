from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import or_

from models import ChartOfAccount

bp = Blueprint("chart_of_accounts", __name__, url_prefix="/api/chart-of-accounts")


@bp.get("")
@jwt_required()
def list_chart_of_accounts():  # type: ignore[override]
    query_text = (request.args.get("query") or "").strip()
    company_raw = request.args.get("company_id")

    company_id: int | None = None
    if company_raw not in {None, ""}:
        try:
            company_id = int(company_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid company identifier."}), 400

    accounts_query = ChartOfAccount.query.filter(ChartOfAccount.is_active.is_(True))

    if company_id:
        accounts_query = accounts_query.filter(
            or_(
                ChartOfAccount.company_id == company_id,
                ChartOfAccount.company_id.is_(None),
            )
        )
    else:
        accounts_query = accounts_query.filter(ChartOfAccount.company_id.is_(None))

    if query_text:
        like = f"%{query_text}%"
        accounts_query = accounts_query.filter(
            or_(
                ChartOfAccount.account_code.ilike(like),
                ChartOfAccount.account_name.ilike(like),
            )
        )

    accounts = accounts_query.order_by(ChartOfAccount.account_code).all()

    return jsonify(
        [
            {
                "id": account.id,
                "account_code": account.account_code,
                "account_name": account.account_name,
                "ifrs_category": account.ifrs_category,
                "ifrs_subcategory": account.ifrs_subcategory,
            }
            for account in accounts
        ]
    )
