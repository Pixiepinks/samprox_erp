"""Service layer helpers for the material module."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from extensions import db
from models import MaterialItem, MRNHeader, Supplier


class MaterialValidationError(Exception):
    """Raised when the payload provided by the client is invalid."""

    def __init__(self, errors: Dict[str, str]):
        super().__init__("Material validation error")
        self.errors = errors


def search_suppliers(query: Optional[str], limit: int = 20) -> list[Supplier]:
    stmt = Supplier.query.order_by(Supplier.name)
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.filter(Supplier.name.ilike(like))
    if limit:
        stmt = stmt.limit(limit)
    return list(stmt)


def create_supplier(payload: Dict[str, Any]) -> Supplier:
    errors: Dict[str, str] = {}
    name = (payload.get("name") or "").strip()
    if not name:
        errors["name"] = "Supplier name is required."
    if errors:
        raise MaterialValidationError(errors)

    supplier = Supplier(
        name=name,
        phone=(payload.get("phone") or None),
        email=(payload.get("email") or None),
        address=(payload.get("address") or None),
        tax_id=(payload.get("tax_id") or None),
    )
    db.session.add(supplier)
    try:
        db.session.commit()
    except IntegrityError as exc:  # pragma: no cover - database error branch
        db.session.rollback()
        if "suppliers_name_key" in str(exc.orig).lower() or "unique constraint" in str(exc.orig).lower():
            raise MaterialValidationError({"name": "A supplier with this name already exists."}) from exc
        raise
    return supplier


def list_material_items(*, search: Optional[str] = None, limit: Optional[int] = None) -> list[MaterialItem]:
    """Return active material items, optionally filtered by ``search``."""

    stmt = MaterialItem.query.filter(MaterialItem.is_active.is_(True)).order_by(MaterialItem.name)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.filter(MaterialItem.name.ilike(like))
    if limit:
        stmt = stmt.limit(limit)
    return list(stmt)


def get_material_item(item_id: uuid.UUID) -> Optional[MaterialItem]:
    return MaterialItem.query.get(item_id)


def create_item(payload: Dict[str, Any]) -> MaterialItem:
    errors: Dict[str, str] = {}
    name = (payload.get("name") or "").strip()
    if not name:
        errors["name"] = "Item name is required."

    is_active_raw = payload.get("is_active", True)
    is_active = bool(is_active_raw) if isinstance(is_active_raw, bool) else str(is_active_raw).lower() != "false"

    if errors:
        raise MaterialValidationError(errors)

    item = MaterialItem(name=name, is_active=is_active)
    db.session.add(item)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        message = str(exc.orig).lower()
        if "unique" in message and "material_items" in message:
            raise MaterialValidationError({"name": "An item with this name already exists."}) from exc
        raise
    return item


def list_recent_mrns(*, search: Optional[str] = None, limit: int = 20) -> list[MRNHeader]:
    stmt = (
        MRNHeader.query.options(
            joinedload(MRNHeader.supplier),
            joinedload(MRNHeader.item),
        )
        .order_by(MRNHeader.date.desc(), MRNHeader.created_at.desc())
    )

    if search:
        term = search.strip()
        if term:
            like = f"%{term}%"
            stmt = (
                stmt.outerjoin(MRNHeader.supplier)
                .outerjoin(MRNHeader.item)
                .filter(
                    or_(
                        MRNHeader.mrn_no.ilike(like),
                        Supplier.name.ilike(like),
                        MaterialItem.name.ilike(like),
                    )
                )
                .distinct()
            )

    if limit:
        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            limit_value = 20
        if limit_value > 0:
            stmt = stmt.limit(min(limit_value, 100))

    return list(stmt)


def _parse_uuid(value: Any, field: str, errors: Dict[str, str]) -> Optional[uuid.UUID]:
    if value in (None, ""):
        errors[field] = "This field is required."
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError):
        errors[field] = "Invalid identifier."
        return None


def _parse_date(value: Any, field: str, errors: Dict[str, str]) -> Optional[date]:
    if not value:
        errors[field] = "This field is required."
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            errors[field] = "Invalid date. Use YYYY-MM-DD."
            return None
    errors[field] = "Invalid date."
    return None


def _parse_datetime(value: Any, field: str, errors: Dict[str, str]) -> Optional[datetime]:
    if not value:
        errors[field] = "This field is required."
        return None
    if isinstance(value, datetime):
        dt_value = value
    elif isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt_value = datetime.fromisoformat(raw)
        except ValueError:
            errors[field] = "Invalid datetime. Use ISO 8601 format."
            return None
    else:
        errors[field] = "Invalid datetime."
        return None

    if dt_value.tzinfo is None:
        dt_value = dt_value.replace(tzinfo=timezone.utc)
    return dt_value


def _decimal_field(
    value: Any,
    field: str,
    errors: Dict[str, str],
    *,
    required: bool = True,
    minimum: Optional[Decimal] = None,
    exclusive_minimum: Optional[Decimal] = None,
    quantize: Optional[str] = None,
    default: Optional[Decimal] = None,
) -> Optional[Decimal]:
    if value in (None, ""):
        if default is not None:
            numeric = default
        elif required:
            errors[field] = "This field is required."
            return None
        else:
            return None
    else:
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, ValueError):
            errors[field] = "Enter a valid number."
            return None

    if minimum is not None and numeric < minimum:
        errors[field] = f"Must be greater than or equal to {minimum}."
        return None
    if exclusive_minimum is not None and numeric <= exclusive_minimum:
        errors[field] = f"Must be greater than {exclusive_minimum}."
        return None

    if quantize:
        numeric = numeric.quantize(Decimal(quantize), rounding=ROUND_HALF_UP)
    return numeric


def create_mrn(payload: Dict[str, Any], *, created_by: Optional[int] = None) -> MRNHeader:
    errors: Dict[str, str] = {}

    mrn_no = (payload.get("mrn_no") or "").strip()
    if not mrn_no:
        errors["mrn_no"] = "MRN number is required."

    weighing_slip_no = (payload.get("weighing_slip_no") or "").strip()
    if not weighing_slip_no:
        errors["weighing_slip_no"] = "Weighing slip number is required."

    security_officer_name = (payload.get("security_officer_name") or "").strip()
    if not security_officer_name:
        errors["security_officer_name"] = "Security officer name is required."

    authorized_person_name = (payload.get("authorized_person_name") or "").strip()
    if not authorized_person_name:
        errors["authorized_person_name"] = "Authorized person name is required."

    supplier_name_free = (payload.get("supplier_name_free") or "").strip() or None

    supplier_id = payload.get("supplier_id")
    supplier_uuid = None
    supplier = None
    if supplier_id:
        supplier_uuid = _parse_uuid(supplier_id, "supplier_id", errors)
        if supplier_uuid:
            supplier = Supplier.query.get(supplier_uuid)
            if not supplier:
                errors["supplier_id"] = "Supplier not found."
    if not supplier_uuid and not supplier_name_free:
        errors["supplier_id"] = "Select a supplier or enter a name."

    item_uuid = _parse_uuid(payload.get("item_id"), "item_id", errors)
    item: Optional[MaterialItem] = None
    if item_uuid:
        item = get_material_item(item_uuid)
        if not item:
            errors["item_id"] = "Item not found."
        elif item.is_active is False:
            errors["item_id"] = "Selected item is inactive."

    mrn_date = _parse_date(payload.get("date"), "date", errors)
    weigh_in_time = _parse_datetime(payload.get("weigh_in_time"), "weigh_in_time", errors)
    weigh_out_time = _parse_datetime(payload.get("weigh_out_time"), "weigh_out_time", errors)

    qty_ton = _decimal_field(
        payload.get("qty_ton"),
        "qty_ton",
        errors,
        exclusive_minimum=Decimal("0"),
        quantize="0.001",
    )
    unit_price = _decimal_field(
        payload.get("unit_price"),
        "unit_price",
        errors,
        minimum=Decimal("0"),
        quantize="0.01",
    )
    wet_factor = _decimal_field(
        payload.get("wet_factor", Decimal("1.000")),
        "wet_factor",
        errors,
        minimum=Decimal("0"),
        quantize="0.001",
        default=Decimal("1.000"),
    )

    if errors:
        raise MaterialValidationError(errors)

    if weigh_in_time and weigh_out_time and weigh_out_time < weigh_in_time:
        errors["weigh_out_time"] = "Weigh-out time must be after weigh-in time."
        raise MaterialValidationError(errors)

    approved_unit_price = (unit_price * wet_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount = (qty_ton * approved_unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    mrn_kwargs: Dict[str, Any] = dict(
        mrn_no=mrn_no,
        date=mrn_date,
        supplier=supplier,
        supplier_name_free=supplier_name_free if not supplier else None,
        item_id=item_uuid,
        qty_ton=qty_ton,
        unit_price=unit_price,
        wet_factor=wet_factor,
        approved_unit_price=approved_unit_price,
        amount=amount,
        weighing_slip_no=weighing_slip_no,
        weigh_in_time=weigh_in_time,
        weigh_out_time=weigh_out_time,
        security_officer_name=security_officer_name,
        authorized_person_name=authorized_person_name,
        created_by=created_by,
    )

    if isinstance(item, MaterialItem):
        mrn_kwargs["item"] = item

    mrn = MRNHeader(**mrn_kwargs)

    db.session.add(mrn)
    try:
        db.session.commit()
    except IntegrityError as exc:  # pragma: no cover - depends on database backend
        db.session.rollback()
        if "mrn_no" in str(exc.orig).lower():
            raise MaterialValidationError({"mrn_no": "MRN number already exists."}) from exc
        raise

    return mrn


def get_mrn_detail(mrn_id: Any) -> MRNHeader:
    try:
        mrn_uuid = mrn_id if isinstance(mrn_id, uuid.UUID) else uuid.UUID(str(mrn_id))
    except (ValueError, TypeError):
        raise MaterialValidationError({"id": "Invalid MRN identifier."})

    mrn = (
        MRNHeader.query.options(
            joinedload(MRNHeader.supplier),
            joinedload(MRNHeader.item),
        )
        .filter(MRNHeader.id == mrn_uuid)
        .first()
    )
    if not mrn:
        raise MaterialValidationError({"id": "MRN not found."})
    return mrn


def seed_material_defaults() -> None:
    """Ensure a baseline set of material items exists."""

    default_items = [
        "Wood Shaving",
        "Saw Dust",
        "Wood Powder",
        "Peanut Husk",
    ]

    for name in default_items:
        exists = (
            MaterialItem.query.filter(func.lower(MaterialItem.name) == name.lower())
            .with_entities(MaterialItem.id)
            .first()
        )
        if not exists:
            db.session.add(MaterialItem(name=name, is_active=True))

    db.session.commit()
