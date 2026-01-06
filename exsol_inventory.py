from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

from flask import current_app
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from exsol_storage import ExsolStorageUnavailable, ExsolStockItem, get_exsol_storage


@dataclass
class ExsolInventoryError(Exception):
    errors: Dict[str, str]

    def __str__(self) -> str:
        return "Exsol inventory validation error"


DEFAULT_EXSOL_ITEMS: List[Dict[str, Any]] = [
    {"item_code": "EXS-PMP-050-1X1", "item_name": 'EXS Water Pump 0.50 HP 1" x 1"', "category": "Water Pump", "hp": "0.50", "size": '1" x 1"'},
    {"item_code": "EXS-PMP-075-1X1", "item_name": 'EXS Water Pump 0.75 HP 1" x 1"', "category": "Water Pump", "hp": "0.75", "size": '1" x 1"'},
    {"item_code": "EXS-PMP-100-1X1", "item_name": 'EXS Water Pump 1.00 HP 1" x 1"', "category": "Water Pump", "hp": "1.00", "size": '1" x 1"'},
    {"item_code": "EXS-PMP-150-1X1", "item_name": 'EXS Water Pump 1.50 HP 1" x 1"', "category": "Water Pump", "hp": "1.50", "size": '1" x 1"'},
    {"item_code": "EXS-PMP-200-2X2", "item_name": 'EXS Water Pump 2.00 HP 2" x 2"', "category": "Water Pump", "hp": "2.00", "size": '2" x 2"'},
    {"item_code": "EXS-PMP-060-1X1", "item_name": 'EXS 60 Water Pump 0.50 HP 1" x 1"', "category": "Water Pump", "hp": "0.50", "size": '1" x 1"'},
    {"item_code": "EXSCU-PCP-050", "item_name": "EXSCU Pressure Controller Pump 0.50 HP", "category": "Pressure Controller Pump", "hp": "0.50"},
    {"item_code": "EXSCU-PCP-075", "item_name": "EXSCU Pressure Controller Pump 0.75 HP", "category": "Pressure Controller Pump", "hp": "0.75"},
    {"item_code": "EXSCU-PCP-100", "item_name": "EXSCU Pressure Controller Pump 1.00 HP", "category": "Pressure Controller Pump", "hp": "1.00"},
    {"item_code": "EXSCU-PCP-060", "item_name": "EXSCU 60 Pressure Controller Pump", "category": "Pressure Controller Pump"},
    {"item_code": "ACC-PC-220-15B-BLU", "item_name": "Pressure Control 220–240V 1.5 bar (Blue)", "category": "Accessory", "voltage": "220–240V", "pressure_bar": "1.5", "variant": "Blue"},
    {"item_code": "ACC-PC-220-15B-LCL", "item_name": "Pressure Control 220–240V 1.5 bar (LCL)", "category": "Accessory", "voltage": "220–240V", "pressure_bar": "1.5", "variant": "LCL"},
]


def _get_session() -> Session:
    storage = get_exsol_storage()
    return storage.session()


def _parse_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def validate_item_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    code = (payload.get("item_code") or "").strip()
    name = (payload.get("item_name") or "").strip()
    category = (payload.get("category") or "").strip()

    if not code:
        errors["item_code"] = "Item code is required."
    if not name:
        errors["item_name"] = "Item name is required."
    if not category:
        errors["category"] = "Category is required."

    if "hp" in payload and payload.get("hp") not in (None, "") and _parse_decimal(payload.get("hp")) is None:
        errors["hp"] = "hp must be numeric."
    if "pressure_bar" in payload and payload.get("pressure_bar") not in (None, "") and _parse_decimal(payload.get("pressure_bar")) is None:
        errors["pressure_bar"] = "pressure_bar must be numeric."

    return errors


def upsert_exsol_item(payload: Dict[str, Any], *, session: Optional[Session] = None) -> ExsolStockItem:
    errors = validate_item_payload(payload)
    if errors:
        raise ExsolInventoryError(errors)

    code = (payload.get("item_code") or "").strip()
    managed_session = session is None
    session = session or _get_session()

    try:
        existing = (
            session.query(ExsolStockItem)
            .filter(func.lower(ExsolStockItem.item_code) == code.lower())
            .one_or_none()
        )

        hp_val = _parse_decimal(payload.get("hp"))
        pressure_val = _parse_decimal(payload.get("pressure_bar"))

        fields = {
            "item_name": (payload.get("item_name") or "").strip(),
            "category": (payload.get("category") or "").strip(),
            "hp": hp_val,
            "size": (payload.get("size") or "").strip() or None,
            "voltage": (payload.get("voltage") or "").strip() or None,
            "pressure_bar": pressure_val,
            "variant": (payload.get("variant") or "").strip() or None,
            "unit": (payload.get("unit") or "").strip() or "NOS",
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
            target = ExsolStockItem(item_code=code, **fields)
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


def list_exsol_items(search: Optional[str] = None, limit: int = 200) -> list[ExsolStockItem]:
    session = _get_session()
    try:
        stmt = session.query(ExsolStockItem)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.filter(
                or_(
                    ExsolStockItem.item_code.ilike(like),
                    ExsolStockItem.item_name.ilike(like),
                    ExsolStockItem.category.ilike(like),
                    ExsolStockItem.variant.ilike(like),
                )
            )
        limit = max(1, min(limit, 500))
        items = (
            stmt.order_by(ExsolStockItem.item_name.asc(), ExsolStockItem.item_code.asc())
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

    session = _get_session()
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
