from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Company, ExsolInventoryItem


@dataclass
class ExsolInventoryError(Exception):
    errors: Dict[str, str]

    def __str__(self) -> str:
        return "Exsol inventory validation error"


EXSOL_COMPANY_NAME = "Exsol Engineering (Pvt) Ltd"


DEFAULT_EXSOL_ITEMS: List[Dict[str, Any]] = [
    {"item_code": "EXS-PMP-050-1X1", "item_name": 'EXS Water Pump 0.50 HP 1" x 1"', "uom": "NOS"},
    {"item_code": "EXS-PMP-075-1X1", "item_name": 'EXS Water Pump 0.75 HP 1" x 1"', "uom": "NOS"},
    {"item_code": "EXS-PMP-100-1X1", "item_name": 'EXS Water Pump 1.00 HP 1" x 1"', "uom": "NOS"},
    {"item_code": "EXS-PMP-150-1X1", "item_name": 'EXS Water Pump 1.50 HP 1" x 1"', "uom": "NOS"},
    {"item_code": "EXS-PMP-200-2X2", "item_name": 'EXS Water Pump 2.00 HP 2" x 2"', "uom": "NOS"},
    {"item_code": "EXS-PMP-060-1X1", "item_name": 'EXS 60 Water Pump 0.50 HP 1" x 1"', "uom": "NOS"},
    {"item_code": "EXSCU-PCP-050", "item_name": "EXSCU Pressure Controller Pump 0.50 HP", "uom": "NOS"},
    {"item_code": "EXSCU-PCP-075", "item_name": "EXSCU Pressure Controller Pump 0.75 HP", "uom": "NOS"},
    {"item_code": "EXSCU-PCP-100", "item_name": "EXSCU Pressure Controller Pump 1.00 HP", "uom": "NOS"},
    {"item_code": "EXSCU-PCP-060", "item_name": "EXSCU 60 Pressure Controller Pump", "uom": "NOS"},
    {"item_code": "ACC-PC-220-15B-BLU", "item_name": "Pressure Control 220–240V 1.5 bar (Blue)", "uom": "NOS"},
    {"item_code": "ACC-PC-220-15B-LCL", "item_name": "Pressure Control 220–240V 1.5 bar (LCL)", "uom": "NOS"},
]


def _get_company_id() -> int:
    company = Company.query.filter(Company.name == EXSOL_COMPANY_NAME).one_or_none()
    if not company:
        raise ExsolInventoryError({"company": "Exsol company is not configured."})
    return company.id


def validate_item_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    code = (payload.get("item_code") or "").strip()
    name = (payload.get("item_name") or "").strip()

    if not code:
        errors["item_code"] = "Item code is required."
    if not name:
        errors["item_name"] = "Item name is required."

    return errors


def upsert_exsol_item(payload: Dict[str, Any], *, session=None) -> ExsolInventoryItem:
    errors = validate_item_payload(payload)
    if errors:
        raise ExsolInventoryError(errors)

    code = (payload.get("item_code") or "").strip()
    company_id = _get_company_id()
    managed_session = session is None
    session = session or db.session

    try:
        existing = (
            session.query(ExsolInventoryItem)
            .filter(ExsolInventoryItem.company_id == company_id)
            .filter(func.lower(ExsolInventoryItem.item_code) == code.lower())
            .one_or_none()
        )

        fields = {
            "item_name": (payload.get("item_name") or "").strip(),
            "uom": (payload.get("uom") or "").strip() or None,
            "is_active": bool(payload.get("is_active", True)),
        }

        if existing:
            for field, value in fields.items():
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            session.add(existing)
            target = existing
        else:
            target = ExsolInventoryItem(company_id=company_id, item_code=code, **fields)
            session.add(target)

        if managed_session:
            session.commit()
        return target
    except SQLAlchemyError:
        if managed_session:
            session.rollback()
        raise
    finally:
        if managed_session:
            try:
                session.close()
            except Exception:
                pass


def list_exsol_items(search: Optional[str] = None, limit: int = 200) -> list[ExsolInventoryItem]:
    company_id = _get_company_id()
    session = db.session
    try:
        stmt = session.query(ExsolInventoryItem).filter(ExsolInventoryItem.company_id == company_id)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.filter(
                or_(
                    ExsolInventoryItem.item_code.ilike(like),
                    ExsolInventoryItem.item_name.ilike(like),
                )
            )
        limit = max(1, min(limit, 500))
        items = (
            stmt.order_by(ExsolInventoryItem.item_name.asc(), ExsolInventoryItem.item_code.asc())
            .limit(limit)
            .all()
        )
        return items
    finally:
        try:
            session.close()
        except Exception:
            pass


def seed_exsol_defaults() -> int:
    """Idempotently seed the default Exsol catalog."""

    session = db.session
    created_or_updated = 0
    try:
        for payload in DEFAULT_EXSOL_ITEMS:
            upsert_exsol_item(payload, session=session)
            created_or_updated += 1
        session.commit()
        return created_or_updated
    except Exception:
        session.rollback()
        raise
    finally:
        try:
            session.close()
        except Exception:
            pass
