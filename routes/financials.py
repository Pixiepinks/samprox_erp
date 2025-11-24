import re
from decimal import Decimal
from typing import Any, Dict

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from extensions import db
from models import (
    Company,
    FinancialTrialBalanceLine,
    IFRS_TRIAL_BALANCE_CATEGORIES,
    generate_financial_year_months,
)

bp = Blueprint("financials", __name__, url_prefix="/api/financial-statements")


IFRS_CATEGORY_SUBCATEGORIES: dict[str, list[str]] = IFRS_TRIAL_BALANCE_CATEGORIES


def _parse_financial_year(value: str | None) -> int:
    if not value:
        return 0
    match = re.match(r"^(\d{4})", value)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _format_financial_year(start_year: int) -> str:
    return f"{start_year}-{start_year + 1}" if start_year else ""


def _month_index_lookup(months: list[dict[str, int | str]]) -> dict[tuple[int, int], int]:
    lookup: dict[tuple[int, int], int] = {}
    for idx, month in enumerate(months, start=1):
        lookup[(int(month["year"]), int(month["month"]))] = idx
    return lookup


def _empty_months() -> dict[int, dict[str, Decimal]]:
    return {i: {"debit": Decimal("0"), "credit": Decimal("0")} for i in range(1, 13)}


def _decimal_from_value(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


@bp.get("/trial-balance")
@jwt_required()
def get_trial_balance():  # type: ignore[override]
    company_id = request.args.get("company_id")
    financial_year_raw = request.args.get("financial_year")

    start_year = _parse_financial_year(financial_year_raw)
    if not start_year:
        return jsonify({"error": "Invalid financial year."}), 400

    financial_year = _format_financial_year(start_year)
    months = generate_financial_year_months(start_year)
    month_lookup = _month_index_lookup(months)

    is_group = str(company_id) == "group"
    company_filter = None
    if not is_group:
        try:
            company_filter = int(company_id) if company_id is not None else None
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid company identifier."}), 400

    query = FinancialTrialBalanceLine.query.filter_by(financial_year=financial_year)
    if company_filter:
        query = query.filter_by(company_id=company_filter)

    lines_map: dict[tuple[str, str, str, str], Dict[str, Any]] = {}

    for record in query.all():
        idx = month_lookup.get((record.calendar_year, record.calendar_month))
        if not idx:
            continue
        key = (record.account_code, record.account_name, record.ifrs_category, record.ifrs_subcategory)
        if key not in lines_map:
            lines_map[key] = {
                "id": record.id,
                "account_code": record.account_code,
                "account_name": record.account_name,
                "ifrs_category": record.ifrs_category,
                "ifrs_subcategory": record.ifrs_subcategory,
                "months": _empty_months(),
            }
        lines_map[key]["months"][idx] = {
            "debit": _decimal_from_value(record.debit_amount),
            "credit": _decimal_from_value(record.credit_amount),
        }

    lines = []
    for value in lines_map.values():
        months_payload = {
            str(idx): {
                "debit": float(value["months"][idx]["debit"]),
                "credit": float(value["months"][idx]["credit"]),
            }
            for idx in sorted(value["months"].keys())
        }
        lines.append({**{k: v for k, v in value.items() if k != "months"}, "months": months_payload})

    totals = {idx: {"debit": Decimal("0"), "credit": Decimal("0")} for idx in range(1, 13)}
    for value in lines_map.values():
        for idx, amounts in value["months"].items():
            totals[idx]["debit"] += _decimal_from_value(amounts["debit"])
            totals[idx]["credit"] += _decimal_from_value(amounts["credit"])

    totals_payload = {
        str(idx): {"debit": float(data["debit"]), "credit": float(data["credit"])}
        for idx, data in totals.items()
    }

    return jsonify(
        {
            "months": [
                {
                    "month_index": idx,
                    "label": month["label"],
                    "calendar_year": month["year"],
                    "calendar_month": month["month"],
                }
                for idx, month in enumerate(months, start=1)
            ],
            "lines": lines,
            "totals": totals_payload,
            "ifrs_categories": IFRS_CATEGORY_SUBCATEGORIES,
        }
    )


@bp.post("/trial-balance")
@jwt_required()
def save_trial_balance():  # type: ignore[override]
    payload = request.get_json(silent=True) or {}
    company_id_raw = payload.get("company_id")
    financial_year_raw = payload.get("financial_year")
    lines = payload.get("lines") or []

    start_year = _parse_financial_year(financial_year_raw)
    if not start_year:
        return jsonify({"error": "Invalid financial year."}), 400

    financial_year = _format_financial_year(start_year)

    if company_id_raw == "group":
        return jsonify({"error": "Select a specific company to save trial balance."}), 400

    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid company selection."}), 400

    company = Company.query.get(company_id)
    if not company:
        return jsonify({"error": "Company not found."}), 404

    if not isinstance(lines, list):
        return jsonify({"error": "Lines payload must be a list."}), 400

    months = generate_financial_year_months(start_year)

    def _build_rows() -> list[FinancialTrialBalanceLine]:
        new_rows: list[FinancialTrialBalanceLine] = []
        for line in lines:
            account_code = (line.get("account_code") or "").strip()
            account_name = (line.get("account_name") or "").strip()
            ifrs_category = (line.get("ifrs_category") or "").strip()
            ifrs_subcategory = (line.get("ifrs_subcategory") or "").strip()

            if not account_name:
                continue

            months_payload = line.get("months") or {}

            for idx, month in enumerate(months, start=1):
                month_data = months_payload.get(str(idx), {}) if isinstance(months_payload, dict) else {}
                debit = _decimal_from_value(month_data.get("debit"))
                credit = _decimal_from_value(month_data.get("credit"))

                new_rows.append(
                    FinancialTrialBalanceLine(
                        company_id=company.id,
                        financial_year=financial_year,
                        month_index=idx,
                        calendar_year=int(month["year"]),
                        calendar_month=int(month["month"]),
                        account_code=account_code,
                        account_name=account_name,
                        ifrs_category=ifrs_category,
                        ifrs_subcategory=ifrs_subcategory,
                        debit_amount=debit,
                        credit_amount=credit,
                    )
                )
        return new_rows

    new_rows = _build_rows()

    try:
        db.session.query(FinancialTrialBalanceLine).filter_by(
            company_id=company.id, financial_year=financial_year
        ).delete()
        db.session.bulk_save_objects(new_rows)
        db.session.commit()
    except Exception as exc:  # pragma: no cover - safety net for DB failures
        db.session.rollback()
        return jsonify({"error": f"Failed to save trial balance: {exc}"}), 500

    totals = {
        str(idx): {
            "debit": float(sum(r.debit_amount for r in new_rows if r.month_index == idx)),
            "credit": float(sum(r.credit_amount for r in new_rows if r.month_index == idx)),
        }
        for idx in range(1, 13)
    }

    return jsonify({"ok": True, "totals": totals})
