"""Utilities for briquette production mix tracking and FIFO costing."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Deque, Dict, Iterable, List, Optional

from sqlalchemy import case, func

from extensions import db
from models import (
    BriquetteMixEntry,
    DailyProductionEntry,
    MachineAsset,
    MaterialItem,
    MRNHeader,
    MRNLine,
)

TON_TO_KG = Decimal("1000")
TON_QUANT = Decimal("0.001")
KG_QUANT = Decimal("0.001")
CURRENCY_QUANT = Decimal("0.01")
UNIT_COST_QUANT = Decimal("0.0001")

BRIQUETTE_MACHINE_CODES = ("MCH-0001", "MCH-0002")
DRYER_MACHINE_CODE = "MCH-0003"
BRIQUETTE_MACHINE_CODES_LOWER = tuple(code.lower() for code in BRIQUETTE_MACHINE_CODES)
DRYER_MACHINE_CODE_LOWER = DRYER_MACHINE_CODE.lower()

MATERIAL_ORDER = [
    "sawdust",
    "wood_shaving",
    "wood_powder",
    "peanut_husk",
    "fire_cut",
]

ENTRY_FIELD_MAP = {
    "sawdust": "sawdust_qty_ton",
    "wood_shaving": "wood_shaving_qty_ton",
    "wood_powder": "wood_powder_qty_ton",
    "peanut_husk": "peanut_husk_qty_ton",
    "fire_cut": "fire_cut_qty_ton",
}

MATERIAL_LABELS = {
    "sawdust": "Sawdust",
    "wood_shaving": "Wood Shaving",
    "wood_powder": "Wood Powder",
    "peanut_husk": "Peanut Husk",
    "fire_cut": "Fire Cut",
}

MATERIAL_NAME_MAP = {
    "sawdust": "sawdust",
    "woodshaving": "wood_shaving",
    "woodshavings": "wood_shaving",
    "woodpowder": "wood_powder",
    "peanuthusk": "peanut_husk",
    "firecut": "fire_cut",
}

OPENING_STOCKS_KG = {
    "peanut_husk": (Decimal("0"), Decimal("0")),
    "sawdust": (Decimal("18195"), Decimal("8.12")),
    "wood_powder": (Decimal("0"), Decimal("0")),
    "wood_shaving": (Decimal("340298"), Decimal("9.80")),
    "fire_cut": (Decimal("2000"), Decimal("8.89")),
}

DEFAULT_BRIQUETTE_ENTRY_LIMIT = 120
MAX_BRIQUETTE_ENTRY_LIMIT = 365


@dataclass
class InventoryLayer:
    quantity_kg: Decimal
    unit_cost: Decimal


@dataclass
class ReceiptLayer:
    date: date
    quantity_kg: Decimal
    unit_cost: Decimal


def _quantize(value: Decimal, quant: Decimal) -> Decimal:
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _decimal_from_value(value: object, *, default: str = "0") -> Decimal:
    text = default if value in (None, "") else str(value)
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _canonical_material_key(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    normalized = "".join(ch for ch in name.lower() if ch.isalnum())
    return MATERIAL_NAME_MAP.get(normalized)


def _load_receipt_layers() -> Dict[str, Deque[ReceiptLayer]]:
    rows = (
        db.session.query(
            MRNHeader.date.label("header_date"),
            MRNHeader.created_at.label("header_created_at"),
            MRNLine.created_at.label("line_created_at"),
            MaterialItem.name.label("item_name"),
            MRNLine.qty_ton,
            MRNLine.approved_unit_price,
            MRNLine.unit_price,
        )
        .join(MRNLine, MRNLine.mrn_id == MRNHeader.id)
        .join(MaterialItem, MRNLine.item_id == MaterialItem.id)
        .order_by(
            MRNHeader.date.asc(),
            MRNHeader.created_at.asc(),
            MRNLine.created_at.asc(),
        )
        .all()
    )

    receipts: Dict[str, Deque[ReceiptLayer]] = {key: deque() for key in MATERIAL_ORDER}

    for row in rows:
        key = _canonical_material_key(getattr(row, "item_name", None))
        if key not in receipts:
            continue

        qty_ton = _decimal_from_value(getattr(row, "qty_ton", None))
        if qty_ton <= 0:
            continue

        qty_kg = _quantize(qty_ton * TON_TO_KG, KG_QUANT)
        price_value = _decimal_from_value(
            getattr(row, "approved_unit_price", None), default=str(getattr(row, "unit_price", "0"))
        )
        if price_value < 0:
            price_value = Decimal("0")
        unit_cost = _quantize(price_value / TON_TO_KG, UNIT_COST_QUANT)

        layer_date = getattr(row, "header_date", None)
        if not layer_date:
            created_at = getattr(row, "line_created_at", None) or getattr(row, "header_created_at", None)
            layer_date = created_at.date() if created_at else date.today()

        receipts[key].append(
            ReceiptLayer(
                date=layer_date,
                quantity_kg=qty_kg,
                unit_cost=unit_cost,
            )
        )

    return receipts


def _load_production_for_dates(dates: Iterable[date]) -> Dict[date, Dict[str, Decimal]]:
    dates = list({value for value in dates if isinstance(value, date)})
    if not dates:
        return {}

    rows = (
        db.session.query(
            DailyProductionEntry.date.label("prod_date"),
            func.coalesce(
                func.sum(
                    case(
                        (func.lower(MachineAsset.code).in_(BRIQUETTE_MACHINE_CODES_LOWER), DailyProductionEntry.quantity_tons),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("briquette_tons"),
            func.coalesce(
                func.sum(
                    case(
                        (func.lower(MachineAsset.code) == DRYER_MACHINE_CODE_LOWER, DailyProductionEntry.quantity_tons),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("dryer_tons"),
        )
        .join(MachineAsset, DailyProductionEntry.asset_id == MachineAsset.id)
        .filter(DailyProductionEntry.date.in_(dates))
        .group_by(DailyProductionEntry.date)
        .all()
    )

    production: Dict[date, Dict[str, Decimal]] = {}
    for row in rows:
        prod_date = getattr(row, "prod_date", None)
        if not isinstance(prod_date, date):
            continue
        briquette = _decimal_from_value(getattr(row, "briquette_tons", 0))
        dryer = _decimal_from_value(getattr(row, "dryer_tons", 0))
        production[prod_date] = {
            "briquette_tons": briquette,
            "dryer_tons": dryer,
        }
    return production


def _build_inventory_from_opening() -> Dict[str, Deque[InventoryLayer]]:
    inventory: Dict[str, Deque[InventoryLayer]] = {key: deque() for key in MATERIAL_ORDER}
    for key, (qty_kg, unit_price) in OPENING_STOCKS_KG.items():
        quantity = Decimal(qty_kg)
        cost = Decimal(unit_price)
        if quantity > 0:
            inventory[key].append(InventoryLayer(quantity_kg=_quantize(quantity, KG_QUANT), unit_cost=cost))
    return inventory


def _initial_last_unit_costs() -> Dict[str, Decimal]:
    lookup: Dict[str, Decimal] = {}
    for key, (_, unit_price) in OPENING_STOCKS_KG.items():
        lookup[key] = Decimal(unit_price)
    return lookup


def _consume_material(
    inventory: Dict[str, Deque[InventoryLayer]],
    last_unit_cost: Dict[str, Decimal],
    material_key: str,
    quantity_kg: Decimal,
) -> Decimal:
    if quantity_kg <= 0:
        return Decimal("0")

    remaining = quantity_kg
    cost = Decimal("0")
    layers = inventory[material_key]

    while remaining > 0 and layers:
        layer = layers[0]
        take = remaining if layer.quantity_kg >= remaining else layer.quantity_kg
        if take > 0:
            cost += take * layer.unit_cost
            layer.quantity_kg -= take
            remaining -= take
        if layer.quantity_kg <= Decimal("0"):
            layers.popleft()

    if remaining > 0:
        fallback = last_unit_cost.get(material_key, Decimal("0"))
        cost += remaining * fallback
        remaining = Decimal("0")

    if layers:
        last_unit_cost[material_key] = layers[0].unit_cost

    return cost


def _ensure_breakdown_structure() -> Dict[str, Dict[str, str]]:
    return {
        key: {
            "label": MATERIAL_LABELS[key],
            "quantity_kg": "0.000",
            "quantity_ton": "0.000",
            "unit_price": "0.0000",
            "total_cost": "0.00",
        }
        for key in MATERIAL_ORDER
    }


def _recalculate_fifo_costs(production_map: Optional[Dict[date, Dict[str, Decimal]]] = None) -> None:
    entries = BriquetteMixEntry.query.order_by(BriquetteMixEntry.date.asc()).all()
    if not entries:
        return

    if production_map is None:
        production_map = _load_production_for_dates(entry.date for entry in entries)

    inventory = _build_inventory_from_opening()
    last_unit_cost = _initial_last_unit_costs()
    receipts = _load_receipt_layers()

    pending_receipts: Dict[str, Deque[ReceiptLayer]] = {key: deque(value) for key, value in receipts.items()}

    for entry in entries:
        entry_breakdown = _ensure_breakdown_structure()
        production = production_map.get(entry.date, {"briquette_tons": Decimal("0"), "dryer_tons": Decimal("0")})
        briquette_tons = production.get("briquette_tons", Decimal("0"))
        output_kg = _quantize(briquette_tons * TON_TO_KG, KG_QUANT)

        for key in MATERIAL_ORDER:
            queue = pending_receipts[key]
            while queue and queue[0].date <= entry.date:
                receipts_layer = queue.popleft()
                inventory[key].append(
                    InventoryLayer(
                        quantity_kg=_quantize(receipts_layer.quantity_kg, KG_QUANT),
                        unit_cost=receipts_layer.unit_cost,
                    )
                )
                last_unit_cost[key] = receipts_layer.unit_cost

        total_cost = Decimal("0")

        for key, attr in ENTRY_FIELD_MAP.items():
            quantity_ton = _decimal_from_value(getattr(entry, attr, "0"))
            quantity_ton = _quantize(quantity_ton, TON_QUANT)
            setattr(entry, attr, quantity_ton)
            quantity_kg = _quantize(quantity_ton * TON_TO_KG, KG_QUANT)
            cost = _consume_material(inventory, last_unit_cost, key, quantity_kg)
            cost = _quantize(cost, CURRENCY_QUANT)
            total_cost += cost

            unit_cost = Decimal("0")
            if quantity_kg > 0:
                unit_cost = _quantize(cost / quantity_kg, UNIT_COST_QUANT)
            else:
                unit_cost = _quantize(last_unit_cost.get(key, Decimal("0")), UNIT_COST_QUANT)

            entry_breakdown[key] = {
                "label": MATERIAL_LABELS[key],
                "quantity_kg": f"{_quantize(quantity_kg, KG_QUANT):.3f}",
                "quantity_ton": f"{quantity_ton:.3f}",
                "unit_price": f"{unit_cost:.4f}",
                "total_cost": f"{cost:.2f}",
            }

        entry.total_material_cost = _quantize(total_cost, CURRENCY_QUANT)
        entry.total_output_kg = output_kg
        entry.unit_cost_per_kg = (
            _quantize(total_cost / output_kg, UNIT_COST_QUANT) if output_kg > 0 else Decimal("0.0000")
        )
        entry.cost_breakdown = entry_breakdown

    db.session.flush()


def list_briquette_production_entries(
    *, limit: int = DEFAULT_BRIQUETTE_ENTRY_LIMIT
) -> Dict[str, object]:
    try:
        normalized_limit = max(1, min(int(limit), MAX_BRIQUETTE_ENTRY_LIMIT))
    except (TypeError, ValueError):
        normalized_limit = DEFAULT_BRIQUETTE_ENTRY_LIMIT

    rows = (
        db.session.query(
            DailyProductionEntry.date.label("prod_date"),
            func.coalesce(
                func.sum(
                    case(
                        (func.lower(MachineAsset.code).in_(BRIQUETTE_MACHINE_CODES_LOWER), DailyProductionEntry.quantity_tons),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("briquette_tons"),
            func.coalesce(
                func.sum(
                    case(
                        (func.lower(MachineAsset.code) == DRYER_MACHINE_CODE_LOWER, DailyProductionEntry.quantity_tons),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("dryer_tons"),
        )
        .join(MachineAsset, DailyProductionEntry.asset_id == MachineAsset.id)
        .group_by(DailyProductionEntry.date)
        .order_by(DailyProductionEntry.date.desc())
        .limit(normalized_limit)
        .all()
    )

    dates = [getattr(row, "prod_date") for row in rows if isinstance(getattr(row, "prod_date"), date)]
    mixes = {}
    if dates:
        entries = BriquetteMixEntry.query.filter(BriquetteMixEntry.date.in_(dates)).all()
        mixes = {entry.date: entry for entry in entries}

    results: List[Dict[str, object]] = []
    for row in rows:
        prod_date = getattr(row, "prod_date", None)
        if not isinstance(prod_date, date):
            continue
        briquette_tons = _quantize(_decimal_from_value(getattr(row, "briquette_tons", 0)), TON_QUANT)
        dryer_tons = _quantize(_decimal_from_value(getattr(row, "dryer_tons", 0)), TON_QUANT)
        entry = mixes.get(prod_date)

        material_values = {}
        for key, attr in ENTRY_FIELD_MAP.items():
            raw_value = _decimal_from_value(getattr(entry, attr, "0")) if entry else Decimal("0")
            material_values[key] = float(_quantize(raw_value, TON_QUANT))

        dry_factor_value = Decimal("0")
        if entry and entry.dry_factor is not None:
            dry_factor_value = _decimal_from_value(entry.dry_factor)

        results.append(
            {
                "date": prod_date.isoformat(),
                "briquette_production_ton": float(briquette_tons),
                "dryer_production_ton": float(dryer_tons),
                "materials": material_values,
                "unit_cost_per_kg": float(_quantize(_decimal_from_value(entry.unit_cost_per_kg if entry else 0), UNIT_COST_QUANT)),
                "total_material_cost": float(_quantize(_decimal_from_value(entry.total_material_cost if entry else 0), CURRENCY_QUANT)),
                "dry_factor": float(_quantize(dry_factor_value, UNIT_COST_QUANT)),
                "has_mix": entry is not None,
            }
        )

    return {
        "entries": results,
        "material_order": MATERIAL_ORDER,
        "material_labels": MATERIAL_LABELS,
    }


def _serialize_cost_breakdown(breakdown: Dict[str, Dict[str, str]]) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for key in MATERIAL_ORDER:
        detail = breakdown.get(key, {}) if breakdown else {}
        quantity_kg = _decimal_from_value(detail.get("quantity_kg", "0"))
        quantity_ton = _decimal_from_value(detail.get("quantity_ton", "0"))
        unit_price = _decimal_from_value(detail.get("unit_price", "0"))
        total_cost = _decimal_from_value(detail.get("total_cost", "0"))
        rows.append(
            {
                "key": key,
                "label": MATERIAL_LABELS[key],
                "quantity_kg": float(_quantize(quantity_kg, KG_QUANT)),
                "quantity_ton": float(_quantize(quantity_ton, TON_QUANT)),
                "unit_price": float(_quantize(unit_price, UNIT_COST_QUANT)),
                "total_cost": float(_quantize(total_cost, CURRENCY_QUANT)),
            }
        )
    return rows


def _serialize_mix_entry(
    target_date: date,
    entry: Optional[BriquetteMixEntry],
    production: Dict[str, Decimal],
) -> Dict[str, object]:
    briquette_tons = _quantize(production.get("briquette_tons", Decimal("0")), TON_QUANT)
    dryer_tons = _quantize(production.get("dryer_tons", Decimal("0")), TON_QUANT)
    output_kg = _quantize(briquette_tons * TON_TO_KG, KG_QUANT)

    dry_factor = Decimal("0")
    if entry and entry.dry_factor is not None:
        dry_factor = _decimal_from_value(entry.dry_factor)

    sawdust_ton = _quantize(
        _decimal_from_value(entry.sawdust_qty_ton) if entry else dryer_tons * dry_factor,
        TON_QUANT,
    )

    materials = {}
    for key, attr in ENTRY_FIELD_MAP.items():
        value = _decimal_from_value(getattr(entry, attr, "0")) if entry else Decimal("0")
        materials[key] = float(_quantize(value, TON_QUANT))

    total_cost = _decimal_from_value(entry.total_material_cost) if entry else Decimal("0")
    unit_cost = _decimal_from_value(entry.unit_cost_per_kg) if entry else Decimal("0")
    breakdown = _serialize_cost_breakdown(entry.cost_breakdown if entry else None)

    return {
        "date": target_date.isoformat(),
        "briquette_production_ton": float(briquette_tons),
        "dryer_production_ton": float(dryer_tons),
        "briquette_output_kg": float(output_kg),
        "dry_factor": float(_quantize(dry_factor, UNIT_COST_QUANT)),
        "materials": materials,
        "sawdust_ton": float(sawdust_ton),
        "total_material_cost": float(_quantize(total_cost, CURRENCY_QUANT)),
        "unit_cost_per_kg": float(_quantize(unit_cost, UNIT_COST_QUANT)),
        "cost_breakdown": breakdown,
        "material_order": MATERIAL_ORDER,
        "material_labels": MATERIAL_LABELS,
        "updated_at": entry.updated_at.isoformat() if entry and entry.updated_at else None,
    }


def get_briquette_mix_detail(target_date: date) -> Dict[str, object]:
    entry = BriquetteMixEntry.query.filter_by(date=target_date).first()
    production = _load_production_for_dates([target_date]).get(
        target_date,
        {"briquette_tons": Decimal("0"), "dryer_tons": Decimal("0")},
    )
    return _serialize_mix_entry(target_date, entry, production)


def update_briquette_mix(target_date: date, payload: Dict[str, object]) -> Dict[str, object]:
    payload = payload or {}

    def _parse_positive_decimal(field: str, *, places: Decimal = TON_QUANT) -> Decimal:
        value = payload.get(field)
        numeric = _decimal_from_value(value)
        if numeric < 0:
            raise ValueError(f"{field} cannot be negative.")
        return _quantize(numeric, places)

    dry_factor = _parse_positive_decimal("dry_factor", places=UNIT_COST_QUANT)
    wood_shaving = _parse_positive_decimal("wood_shaving_ton")
    wood_powder = _parse_positive_decimal("wood_powder_ton")
    peanut_husk = _parse_positive_decimal("peanut_husk_ton")
    fire_cut = _parse_positive_decimal("fire_cut_ton")

    production_map = _load_production_for_dates([target_date])
    production = production_map.get(
        target_date,
        {"briquette_tons": Decimal("0"), "dryer_tons": Decimal("0")},
    )
    dryer_tons = production.get("dryer_tons", Decimal("0"))
    sawdust = _quantize(dryer_tons * dry_factor, TON_QUANT)

    entry = BriquetteMixEntry.query.filter_by(date=target_date).first()
    if entry is None:
        entry = BriquetteMixEntry(date=target_date)
        db.session.add(entry)

    entry.dry_factor = dry_factor
    entry.wood_shaving_qty_ton = wood_shaving
    entry.wood_powder_qty_ton = wood_powder
    entry.peanut_husk_qty_ton = peanut_husk
    entry.fire_cut_qty_ton = fire_cut
    entry.sawdust_qty_ton = sawdust

    db.session.flush()
    _recalculate_fifo_costs(production_map)
    db.session.commit()
    db.session.refresh(entry)

    production_after = production_map.get(
        target_date,
        {"briquette_tons": Decimal("0"), "dryer_tons": Decimal("0")},
    )
    return _serialize_mix_entry(target_date, entry, production_after)
