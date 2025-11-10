"""Service layer helpers for the material module."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional


DEFAULT_MATERIAL_ITEM_NAMES = [
    "Wood Shaving",
    "Saw Dust",
    "Wood Powder",
    "Peanut Husk",
]

from sqlalchemy import String, cast, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from extensions import db
from models import MaterialItem, MRNHeader, MRNLine, Supplier

SUPPLIER_CATEGORIES = {
    "Raw Material",
    "Packing Material",
    "Repair Material",
    "Maintenance Material",
}

CREDIT_PERIOD_OPTIONS = {"Cash", "3 Days", "7 Days", "1 Month"}

VALID_SOURCING_TYPES = {"Ownsourcing", "Outside"}
INTERNAL_VEHICLE_NUMBERS = {"LI-1795", "LB-3237"}


class MaterialValidationError(Exception):
    """Raised when the payload provided by the client is invalid."""

    def __init__(self, errors: Dict[str, str]):
        super().__init__("Material validation error")
        self.errors = errors


SUPPLIER_REGISTRATION_PREFIX = "SR"

MRN_NUMBER_START = 25392


def _strip_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
    else:
        value = str(value).strip()
    return value or None


def _next_registration_sequence(last_reg_no: Optional[str]) -> int:
    if not last_reg_no:
        return 1
    match = re.search(r"(\d+)$", last_reg_no)
    if not match:
        return 1
    try:
        return int(match.group(1)) + 1
    except ValueError:
        return 1


def _format_registration_no(sequence: int) -> str:
    return f"{SUPPLIER_REGISTRATION_PREFIX}{sequence:04d}"


def get_next_supplier_registration_no() -> str:
    last_reg_no = db.session.query(func.max(Supplier.supplier_reg_no)).scalar()
    return _format_registration_no(_next_registration_sequence(last_reg_no))


def _parse_numeric_mrn(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or not text.isdigit():
        return None
    try:
        return int(text)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def get_next_mrn_number() -> str:
    """Return the next sequential MRN number starting from ``MRN_NUMBER_START``."""

    max_number = MRN_NUMBER_START - 1
    for (value,) in db.session.query(MRNHeader.mrn_no).all():
        parsed = _parse_numeric_mrn(value)
        if parsed is not None and parsed > max_number:
            max_number = parsed

    next_number = max_number + 1
    if next_number < MRN_NUMBER_START:
        next_number = MRN_NUMBER_START
    return str(next_number)


def search_suppliers(query: Optional[str], limit: int = 20) -> list[Supplier]:
    stmt = Supplier.query.order_by(Supplier.name)
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.filter(
            or_(
                Supplier.name.ilike(like),
                Supplier.supplier_reg_no.ilike(like),
                Supplier.supplier_id_no.ilike(like),
                Supplier.primary_phone.ilike(like),
                Supplier.secondary_phone.ilike(like),
                Supplier.vehicle_no_1.ilike(like),
                Supplier.vehicle_no_2.ilike(like),
                Supplier.vehicle_no_3.ilike(like),
                Supplier.email.ilike(like),
                Supplier.address.ilike(like),
                Supplier.tax_id.ilike(like),
                cast(Supplier.id, String).ilike(like),
            )
        )
    if limit:
        stmt = stmt.limit(limit)
    return list(stmt)


def _is_unique_violation(exc: IntegrityError, *keywords: str) -> bool:
    """Return ``True`` if ``exc`` represents a unique constraint violation."""

    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None)
    message_detail = getattr(diag, "message_detail", None)

    haystacks: list[str] = []
    if constraint_name:
        haystacks.append(constraint_name.lower())
    if message_detail:
        haystacks.append(message_detail.lower())
    if orig is not None:
        haystacks.append(str(orig).lower())
    else:
        haystacks.append(str(exc).lower())

    lowered_keywords = [keyword.lower() for keyword in keywords]
    for haystack in haystacks:
        if haystack and all(keyword in haystack for keyword in lowered_keywords):
            return True
    return False


def create_supplier(payload: Dict[str, Any]) -> Supplier:
    errors: Dict[str, str] = {}
    name = (payload.get("name") or "").strip()
    if not name:
        errors["name"] = "Supplier name is required."

    primary_phone_raw = payload.get("primary_phone")
    primary_phone = (primary_phone_raw or "").strip() if isinstance(primary_phone_raw, str) else str(primary_phone_raw or "").strip()
    if not primary_phone:
        errors["primary_phone"] = "Primary phone is required."

    secondary_phone = _strip_or_none(payload.get("secondary_phone"))

    category = (payload.get("category") or "").strip()
    if not category:
        errors["category"] = "Category is required."
    elif category not in SUPPLIER_CATEGORIES:
        errors["category"] = "Invalid category."

    vehicle_no_1 = _strip_or_none(payload.get("vehicle_no_1"))
    vehicle_no_2 = _strip_or_none(payload.get("vehicle_no_2"))
    vehicle_no_3 = _strip_or_none(payload.get("vehicle_no_3"))

    if category == "Raw Material" and not vehicle_no_1:
        errors["vehicle_no_1"] = "Vehicle number is required for Raw Material suppliers."

    supplier_id_no = (payload.get("supplier_id_no") or "").strip()
    if not supplier_id_no:
        errors["supplier_id_no"] = "Supplier ID number is required."

    credit_period = (payload.get("credit_period") or "").strip()
    if not credit_period:
        errors["credit_period"] = "Credit period is required."
    elif credit_period not in CREDIT_PERIOD_OPTIONS:
        errors["credit_period"] = "Invalid credit period."

    email = _strip_or_none(payload.get("email"))
    address = _strip_or_none(payload.get("address"))
    tax_id = _strip_or_none(payload.get("tax_id"))

    if errors:
        raise MaterialValidationError(errors)

    supplier = Supplier(
        name=name,
        primary_phone=primary_phone,
        secondary_phone=secondary_phone,
        category=category,
        vehicle_no_1=vehicle_no_1,
        vehicle_no_2=vehicle_no_2,
        vehicle_no_3=vehicle_no_3,
        supplier_id_no=supplier_id_no,
        supplier_reg_no=get_next_supplier_registration_no(),
        credit_period=credit_period,
        email=email,
        address=address,
        tax_id=tax_id,
    )
    db.session.add(supplier)
    registration_attempts = 0
    while True:
        try:
            db.session.commit()
            break
        except IntegrityError as exc:  # pragma: no cover - database error branch
            db.session.rollback()
            if _is_unique_violation(exc, "suppliers", "name"):
                raise MaterialValidationError({"name": "A supplier with this name already exists."}) from exc
            if _is_unique_violation(exc, "supplier_reg_no"):
                registration_attempts += 1
                if registration_attempts >= 5:
                    raise MaterialValidationError(
                        {"supplier_reg_no": "Unable to assign a registration number. Please try again."}
                    ) from exc
                supplier.supplier_reg_no = get_next_supplier_registration_no()
                db.session.add(supplier)
                continue
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
            joinedload(MRNHeader.items).joinedload(MRNLine.item),
        )
        .order_by(MRNHeader.date.desc(), MRNHeader.created_at.desc())
    )

    if search:
        term = search.strip()
        if term:
            like = f"%{term}%"
            stmt = (
                stmt.outerjoin(MRNHeader.supplier)
                .outerjoin(MRNLine, MRNLine.mrn_id == MRNHeader.id)
                .outerjoin(MaterialItem, MRNLine.item_id == MaterialItem.id)
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
    auto_generated_mrn = False
    if not mrn_no:
        mrn_no = get_next_mrn_number()
        auto_generated_mrn = True

    weighing_slip_no = (payload.get("weighing_slip_no") or "").strip()
    if not weighing_slip_no:
        errors["weighing_slip_no"] = "Weighing slip number is required."

    security_officer_name = (payload.get("security_officer_name") or "").strip()
    if not security_officer_name:
        errors["security_officer_name"] = "Security officer name is required."

    authorized_person_name = (payload.get("authorized_person_name") or "").strip()
    if not authorized_person_name:
        errors["authorized_person_name"] = "Authorized person name is required."

    supplier_id = payload.get("supplier_id")
    supplier_uuid = None
    supplier = None
    if supplier_id:
        supplier_uuid = _parse_uuid(supplier_id, "supplier_id", errors)
        if supplier_uuid:
            supplier = Supplier.query.get(supplier_uuid)
            if not supplier:
                errors["supplier_id"] = "Supplier not found."
    else:
        errors["supplier_id"] = "Select a supplier."

    sourcing_type = (payload.get("sourcing_type") or "").strip()
    if sourcing_type not in VALID_SOURCING_TYPES:
        errors["sourcing_type"] = "Select a sourcing type."

    supplier_vehicle_numbers: set[str] = set()
    if supplier:
        supplier_vehicle_numbers = {
            value.strip()
            for value in (
                supplier.vehicle_no_1,
                supplier.vehicle_no_2,
                supplier.vehicle_no_3,
            )
            if isinstance(value, str) and value.strip()
        }

    vehicle_no_raw = (payload.get("vehicle_no") or "").strip()
    vehicle_no = vehicle_no_raw or None

    if not vehicle_no:
        errors["vehicle_no"] = "Select a vehicle number."
    elif sourcing_type == "Ownsourcing":
        match = next(
            (
                internal
                for internal in INTERNAL_VEHICLE_NUMBERS
                if internal.lower() == vehicle_no.lower()
            ),
            None,
        )
        if not match:
            errors["vehicle_no"] = "Select a valid internal vehicle."
        else:
            vehicle_no = match
    elif sourcing_type == "Outside":
        if not supplier:
            errors["vehicle_no"] = "Select a supplier before choosing a vehicle."
        else:
            if not supplier_vehicle_numbers:
                errors["vehicle_no"] = "Selected supplier has no registered vehicles."
            else:
                match = next(
                    (
                        registered
                        for registered in supplier_vehicle_numbers
                        if registered.lower() == vehicle_no.lower()
                    ),
                    None,
                )
                if not match:
                    errors["vehicle_no"] = "Select a vehicle registered to the supplier."
                else:
                    vehicle_no = match
    elif "sourcing_type" not in errors:
        errors["vehicle_no"] = "Select a sourcing type."

    mrn_date = _parse_date(payload.get("date"), "date", errors)
    weigh_in_time = _parse_datetime(payload.get("weigh_in_time"), "weigh_in_time", errors)
    weigh_out_time = _parse_datetime(payload.get("weigh_out_time"), "weigh_out_time", errors)

    items_payload = payload.get("items")
    if not isinstance(items_payload, list) or len(items_payload) == 0:
        errors["items"] = "Add at least one item."
        items_payload = []

    validated_lines: list[Dict[str, Any]] = []
    total_qty = Decimal("0")
    total_amount = Decimal("0")

    for index, item_payload in enumerate(items_payload):
        prefix = f"items.{index}."
        item_uuid = _parse_uuid(item_payload.get("item_id"), prefix + "item_id", errors)
        material_item: Optional[MaterialItem] = None
        if item_uuid:
            material_item = MaterialItem.query.get(item_uuid)
            if not material_item:
                errors[prefix + "item_id"] = "Item not found."
                material_item = None
            elif material_item.is_active is False:
                errors[prefix + "item_id"] = "Selected item is inactive."

        first_weight = _decimal_field(
            item_payload.get("first_weight_kg"),
            prefix + "first_weight_kg",
            errors,
            minimum=Decimal("0"),
            quantize="0.001",
        )
        second_weight = _decimal_field(
            item_payload.get("second_weight_kg"),
            prefix + "second_weight_kg",
            errors,
            minimum=Decimal("0"),
            quantize="0.001",
        )
        unit_price = _decimal_field(
            item_payload.get("unit_price"),
            prefix + "unit_price",
            errors,
            minimum=Decimal("0"),
            quantize="0.01",
        )
        wet_factor = _decimal_field(
            item_payload.get("wet_factor", Decimal("1.000")),
            prefix + "wet_factor",
            errors,
            minimum=Decimal("0"),
            quantize="0.001",
            default=Decimal("1.000"),
        )

        if (
            first_weight is not None
            and second_weight is not None
            and first_weight <= second_weight
        ):
            errors[prefix + "second_weight_kg"] = "Second weight must be less than first weight."
            continue

        if (
            first_weight is None
            or second_weight is None
            or unit_price is None
            or wet_factor is None
            or material_item is None
        ):
            continue

        net_weight = (first_weight - second_weight).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        if net_weight <= Decimal("0"):
            errors[prefix + "qty_ton"] = "Quantity must be greater than 0."
            continue

        qty_ton = (net_weight / Decimal("1000")).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        if qty_ton <= Decimal("0"):
            errors[prefix + "qty_ton"] = "Quantity must be greater than 0."
            continue

        approved_unit_price = (unit_price * wet_factor).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        amount = (qty_ton * approved_unit_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        validated_lines.append(
            dict(
                item=material_item,
                item_id=item_uuid,
                first_weight_kg=first_weight,
                second_weight_kg=second_weight,
                qty_ton=qty_ton,
                unit_price=unit_price,
                wet_factor=wet_factor,
                approved_unit_price=approved_unit_price,
                amount=amount,
            )
        )
        total_qty += qty_ton
        total_amount += amount

    if not errors and weigh_in_time and weigh_out_time and weigh_out_time < weigh_in_time:
        errors["weigh_out_time"] = "Weigh-out time must be after weigh-in time."

    if not validated_lines and "items" not in errors:
        errors["items"] = "Add at least one valid item."

    total_qty = total_qty.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    total_amount = total_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if total_qty <= Decimal("0"):
        errors["qty_ton"] = "Quantity must be greater than 0."

    if errors:
        raise MaterialValidationError(errors)

    mrn_kwargs: Dict[str, Any] = dict(
        mrn_no=mrn_no,
        date=mrn_date,
        supplier=supplier,
        sourcing_type=sourcing_type,
        vehicle_no=vehicle_no,
        qty_ton=total_qty,
        amount=total_amount,
        weighing_slip_no=weighing_slip_no,
        weigh_in_time=weigh_in_time,
        weigh_out_time=weigh_out_time,
        security_officer_name=security_officer_name,
        authorized_person_name=authorized_person_name,
        created_by=created_by,
    )

    mrn = MRNHeader(**mrn_kwargs)

    for line_kwargs in validated_lines:
        mrn.items.append(MRNLine(**line_kwargs))

    db.session.add(mrn)

    attempts = 0
    while True:
        try:
            db.session.commit()
            break
        except IntegrityError as exc:  # pragma: no cover - depends on database backend
            db.session.rollback()
            if _is_unique_violation(exc, "mrn", "mrn_no"):
                if auto_generated_mrn:
                    attempts += 1
                    if attempts >= 5:
                        raise MaterialValidationError(
                            {"mrn_no": "Unable to generate a unique MRN number. Please try again."}
                        ) from exc
                    mrn_no = get_next_mrn_number()
                    mrn.mrn_no = mrn_no
                    db.session.add(mrn)
                    continue
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
            joinedload(MRNHeader.items).joinedload(MRNLine.item),
        )
        .filter(MRNHeader.id == mrn_uuid)
        .first()
    )
    if not mrn:
        raise MaterialValidationError({"id": "MRN not found."})
    return mrn


def seed_material_defaults() -> None:
    """Ensure a baseline set of material items exists."""

    for name in DEFAULT_MATERIAL_ITEM_NAMES:
        exists = (
            MaterialItem.query.filter(func.lower(MaterialItem.name) == name.lower())
            .with_entities(MaterialItem.id)
            .first()
        )
        if not exists:
            db.session.add(MaterialItem(name=name, is_active=True))

    db.session.commit()
