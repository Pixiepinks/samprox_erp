"""Service layer helpers for the material module."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from typing import Any, Dict, Iterable, Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import joinedload

from extensions import db
from models import MaterialCategory, MaterialType, MRNHeader, Supplier


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


def _is_missing_is_active_column(error: Exception) -> bool:
    """Return True if the database error indicates the column is absent."""

    raw = getattr(error, "orig", error)
    message = str(raw).lower()
    return "is_active" in message and "material_types" in message


def list_active_material_types(category_id: uuid.UUID) -> list[MaterialType]:
    """Return active material types for a category.

    Older databases might miss the ``is_active`` column.  When that happens the
    initial query raises a database programming error.  We gracefully recover by
    rolling back the failed transaction and retrying without that filter so the
    UI still receives data instead of a 500 HTML error page.
    """

    base_query = MaterialType.query.filter_by(category_id=category_id)
    try:
        return (
            base_query.filter(MaterialType.is_active.is_(True))
            .order_by(MaterialType.name)
            .all()
        )
    except (ProgrammingError, OperationalError) as exc:
        db.session.rollback()
        if not _is_missing_is_active_column(exc):
            raise
        return base_query.order_by(MaterialType.name).all()


def list_recent_mrns(*, search: Optional[str] = None, limit: int = 20) -> list[MRNHeader]:
    stmt = (
        MRNHeader.query.options(
            joinedload(MRNHeader.supplier),
            joinedload(MRNHeader.category),
            joinedload(MRNHeader.material_type),
        )
        .order_by(MRNHeader.date.desc(), MRNHeader.created_at.desc())
    )

    if search:
        term = search.strip()
        if term:
            like = f"%{term}%"
            stmt = (
                stmt.outerjoin(MRNHeader.supplier)
                .outerjoin(MRNHeader.material_type)
                .filter(
                    or_(
                        MRNHeader.mrn_no.ilike(like),
                        Supplier.name.ilike(like),
                        MaterialType.name.ilike(like),
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

    category_uuid = _parse_uuid(payload.get("category_id"), "category_id", errors)
    category = None
    if category_uuid:
        category = MaterialCategory.query.get(category_uuid)
        if not category:
            errors["category_id"] = "Material category not found."

    material_type_uuid = _parse_uuid(payload.get("material_type_id"), "material_type_id", errors)
    material_type = None
    if material_type_uuid:
        material_type = MaterialType.query.get(material_type_uuid)
        if not material_type:
            errors["material_type_id"] = "Material type not found."
        elif not material_type.is_active:
            errors["material_type_id"] = "Selected material type is inactive."

    if category and material_type and material_type.category_id != category.id:
        errors["material_type_id"] = "Material type does not belong to the selected category."

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

    mrn = MRNHeader(
        mrn_no=mrn_no,
        date=mrn_date,
        supplier=supplier,
        supplier_name_free=supplier_name_free if not supplier else None,
        category=category,
        material_type=material_type,
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
            joinedload(MRNHeader.category),
            joinedload(MRNHeader.material_type),
        )
        .filter(MRNHeader.id == mrn_uuid)
        .first()
    )
    if not mrn:
        raise MaterialValidationError({"id": "MRN not found."})
    return mrn


def seed_material_defaults() -> None:
    categories = [
        "Product Material",
        "Packing Material",
        "Repair Material",
        "Maintenance Material",
    ]

    existing = {c.name: c for c in MaterialCategory.query.filter(MaterialCategory.name.in_(categories)).all()}
    for name in categories:
        if name not in existing:
            category = MaterialCategory(name=name)
            db.session.add(category)
            existing[name] = category

    db.session.flush()

    product_category = existing.get("Product Material")
    if product_category:
        for type_name in ["wood shaving", "saw dust", "wood powder", "peanut husk"]:
            match = (
                MaterialType.query.filter_by(category_id=product_category.id, name=type_name)
                .with_entities(MaterialType.id)
                .first()
            )
            if not match:
                db.session.add(MaterialType(category=product_category, name=type_name, is_active=True))

    db.session.commit()
