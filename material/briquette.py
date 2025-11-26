"""Utilities for briquette production mix tracking and FIFO costing."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, case, func

from extensions import db
from models import (
    BriquetteMixEntry,
    DailyProductionEntry,
    MachineAsset,
    MaterialItem,
    MRNHeader,
    MRNLine,
    SalesActualEntry,
)

TON_TO_KG = Decimal("1000")
TON_QUANT = Decimal("0.001")
KG_QUANT = Decimal("0.001")
CURRENCY_QUANT = Decimal("0.01")
UNIT_COST_QUANT = Decimal("0.0001")
HOUR_QUANT = Decimal("0.1")

DEFAULT_DRY_FACTOR = Decimal("0.6")

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

BRIQUETTE_OPENING_STOCK_TON = Decimal("15.015")
BRIQUETTE_OPENING_UNIT_COST_PER_TON = Decimal("9900")

STOCK_BASE_DATE = date(2025, 9, 30)

STOCK_STATUS_LABELS = {
    **MATERIAL_LABELS,
    "briquettes": "Briquettes",
    "opening_total": "Opening stock (as at 30-Sep-2025)",
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


@dataclass
class TonInventoryLayer:
    quantity_ton: Decimal
    unit_cost_per_ton: Decimal


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
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                func.lower(MachineAsset.code) == DRYER_MACHINE_CODE_LOWER,
                                DailyProductionEntry.quantity_tons > 0.0,
                            ),
                            1.0,
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("dryer_hours"),
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
        dryer_hours = _decimal_from_value(getattr(row, "dryer_hours", 0))
        production[prod_date] = {
            "briquette_tons": briquette,
            "dryer_tons": dryer,
            "dryer_hours": dryer_hours,
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


def _build_opening_layers_ton() -> Tuple[Dict[str, Deque[TonInventoryLayer]], Tuple[Decimal, Decimal]]:
    inventory: Dict[str, Deque[TonInventoryLayer]] = {key: deque() for key in MATERIAL_ORDER}
    total_qty = Decimal("0")
    total_value = Decimal("0")

    for key in MATERIAL_ORDER:
        qty_kg, unit_price = OPENING_STOCKS_KG.get(key, (Decimal("0"), Decimal("0")))
        qty_ton = _quantize(Decimal(qty_kg) / TON_TO_KG, TON_QUANT)
        unit_cost_per_ton = _quantize(Decimal(unit_price) * TON_TO_KG, CURRENCY_QUANT)
        if qty_ton > 0:
            inventory[key].append(
                TonInventoryLayer(quantity_ton=qty_ton, unit_cost_per_ton=unit_cost_per_ton)
            )
            total_qty += qty_ton
            total_value += _quantize(qty_ton * unit_cost_per_ton, CURRENCY_QUANT)

    briquette_opening_qty = _quantize(BRIQUETTE_OPENING_STOCK_TON, TON_QUANT)
    briquette_opening_cost = _quantize(BRIQUETTE_OPENING_UNIT_COST_PER_TON, CURRENCY_QUANT)
    if briquette_opening_qty > 0:
        total_qty += briquette_opening_qty
        total_value += _quantize(briquette_opening_qty * briquette_opening_cost, CURRENCY_QUANT)

    return inventory, (total_qty, total_value)


def _clone_ton_layers(layers: Dict[str, Deque[TonInventoryLayer]]) -> Dict[str, Deque[TonInventoryLayer]]:
    cloned: Dict[str, Deque[TonInventoryLayer]] = {}
    for key, queue in layers.items():
        cloned[key] = deque(
            TonInventoryLayer(quantity_ton=layer.quantity_ton, unit_cost_per_ton=layer.unit_cost_per_ton)
            for layer in queue
        )
    return cloned


def _consume_ton_layers(layers: Deque[TonInventoryLayer], quantity_ton: Decimal) -> None:
    if quantity_ton <= 0:
        return

    remaining = _quantize(quantity_ton, TON_QUANT)
    zero = Decimal("0")

    while remaining > zero and layers:
        layer = layers[0]
        take = remaining if layer.quantity_ton >= remaining else layer.quantity_ton
        if take > zero:
            layer.quantity_ton = _quantize(layer.quantity_ton - take, TON_QUANT)
            remaining = _quantize(remaining - take, TON_QUANT)
        if layer.quantity_ton <= zero:
            layers.popleft()


def _sum_layers(layers: Deque[TonInventoryLayer]) -> Tuple[Decimal, Decimal]:
    qty = Decimal("0")
    value = Decimal("0")
    for layer in layers:
        qty += layer.quantity_ton
        value += layer.quantity_ton * layer.unit_cost_per_ton
    return _quantize(qty, TON_QUANT), _quantize(value, CURRENCY_QUANT)


def _initial_briquette_layers() -> Deque[TonInventoryLayer]:
    layers: Deque[TonInventoryLayer] = deque()
    qty = _quantize(BRIQUETTE_OPENING_STOCK_TON, TON_QUANT)
    unit_cost = _quantize(BRIQUETTE_OPENING_UNIT_COST_PER_TON, CURRENCY_QUANT)
    if qty > 0:
        layers.append(TonInventoryLayer(quantity_ton=qty, unit_cost_per_ton=unit_cost))
    return layers


def calculate_stock_status(as_of: date) -> Dict[str, object]:
    if not isinstance(as_of, date):
        raise TypeError("as_of must be a date instance")

    normalized_date = as_of
    zero = Decimal("0")

    opening_layers, opening_totals = _build_opening_layers_ton()
    raw_inventory = _clone_ton_layers(opening_layers)
    briquette_layers = _initial_briquette_layers()

    material_opening_balances: Dict[str, Decimal] = {
        key: _sum_layers(raw_inventory[key])[0] for key in MATERIAL_ORDER
    }
    material_purchases: Dict[str, Decimal] = {key: zero for key in MATERIAL_ORDER}
    material_consumption: Dict[str, Decimal] = {key: zero for key in MATERIAL_ORDER}
    briquette_opening_balance = _sum_layers(briquette_layers)[0]
    briquette_production = zero
    briquette_sales = zero

    if normalized_date > STOCK_BASE_DATE:
        receipts_by_date: Dict[date, List[Tuple[str, TonInventoryLayer]]] = defaultdict(list)

        receipt_rows = (
            db.session.query(
                MRNHeader.date.label("mrn_date"),
                MRNHeader.created_at.label("header_created_at"),
                MRNLine.created_at.label("line_created_at"),
                MRNLine.id.label("line_id"),
                MaterialItem.name.label("item_name"),
                MRNLine.qty_ton,
                MRNLine.approved_unit_price,
                MRNLine.unit_price,
            )
            .join(MRNLine, MRNLine.mrn_id == MRNHeader.id)
            .join(MaterialItem, MRNLine.item_id == MaterialItem.id)
            .filter(MRNHeader.date > STOCK_BASE_DATE, MRNHeader.date <= normalized_date)
            .order_by(
                MRNHeader.date.asc(),
                MRNHeader.created_at.asc(),
                MRNLine.created_at.asc(),
                MRNLine.id.asc(),
            )
            .all()
        )

        for row in receipt_rows:
            key = _canonical_material_key(getattr(row, "item_name", None))
            if key not in raw_inventory:
                continue

            qty_ton = _quantize(_decimal_from_value(getattr(row, "qty_ton", None)), TON_QUANT)
            if qty_ton <= zero:
                continue

            approved_price = _decimal_from_value(getattr(row, "approved_unit_price", None))
            if approved_price <= zero:
                approved_price = _decimal_from_value(getattr(row, "unit_price", None))
            if approved_price < zero:
                approved_price = zero
            unit_cost_ton = _quantize(approved_price, CURRENCY_QUANT)

            layer_date = getattr(row, "mrn_date", None)
            if not isinstance(layer_date, date):
                created_at = getattr(row, "line_created_at", None) or getattr(row, "header_created_at", None)
                if isinstance(created_at, datetime):
                    layer_date = created_at.date()
                else:
                    layer_date = normalized_date

            if layer_date > normalized_date:
                continue

            receipts_by_date[layer_date].append(
                (key, TonInventoryLayer(quantity_ton=qty_ton, unit_cost_per_ton=unit_cost_ton))
            )

        consumption_by_date: Dict[date, Dict[str, Decimal]] = defaultdict(dict)
        mix_unit_cost_map: Dict[date, Decimal] = {}

        mix_entries = (
            BriquetteMixEntry.query.filter(
                BriquetteMixEntry.date > STOCK_BASE_DATE,
                BriquetteMixEntry.date <= normalized_date,
            )
            .order_by(BriquetteMixEntry.date.asc())
            .all()
        )

        for entry in mix_entries:
            entry_date = getattr(entry, "date", None)
            if not isinstance(entry_date, date):
                continue

            unit_cost_per_kg = _decimal_from_value(getattr(entry, "unit_cost_per_kg", None))
            if unit_cost_per_kg < zero:
                unit_cost_per_kg = zero
            mix_unit_cost_map[entry_date] = _quantize(unit_cost_per_kg, UNIT_COST_QUANT)

            entry_consumption = consumption_by_date[entry_date]
            for key, attr in ENTRY_FIELD_MAP.items():
                quantity = _quantize(_decimal_from_value(getattr(entry, attr, None)), TON_QUANT)
                if quantity > zero:
                    entry_consumption[key] = _quantize(entry_consumption.get(key, zero) + quantity, TON_QUANT)

        timeline_dates = sorted(set(receipts_by_date.keys()) | set(consumption_by_date.keys()))
        for day in timeline_dates:
            if day < normalized_date:
                for key, layer in receipts_by_date.get(day, []):
                    raw_inventory[key].append(layer)
                for key, quantity in consumption_by_date.get(day, {}).items():
                    _consume_ton_layers(raw_inventory[key], quantity)
                material_opening_balances = {
                    key: _sum_layers(raw_inventory[key])[0] for key in MATERIAL_ORDER
                }
                continue

            if day == normalized_date:
                material_opening_balances = {
                    key: _sum_layers(raw_inventory[key])[0] for key in MATERIAL_ORDER
                }
                for key, layer in receipts_by_date.get(day, []):
                    raw_inventory[key].append(layer)
                    material_purchases[key] = _quantize(
                        material_purchases.get(key, zero) + layer.quantity_ton,
                        TON_QUANT,
                    )
                for key, quantity in consumption_by_date.get(day, {}).items():
                    if quantity > zero:
                        material_consumption[key] = _quantize(
                            material_consumption.get(key, zero) + quantity,
                            TON_QUANT,
                        )
                    _consume_ton_layers(raw_inventory[key], quantity)
                break

            if day > normalized_date:
                break

        briquette_receipts_by_date: Dict[date, List[TonInventoryLayer]] = defaultdict(list)

        production_rows = (
            db.session.query(
                DailyProductionEntry.date.label("prod_date"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                func.lower(MachineAsset.code).in_(BRIQUETTE_MACHINE_CODES_LOWER),
                                DailyProductionEntry.quantity_tons,
                            ),
                            else_=0.0,
                        )
                    ),
                    0.0,
                ).label("briquette_tons"),
            )
            .join(MachineAsset, DailyProductionEntry.asset_id == MachineAsset.id)
            .filter(
                DailyProductionEntry.date > STOCK_BASE_DATE,
                DailyProductionEntry.date <= normalized_date,
            )
            .group_by(DailyProductionEntry.date)
            .order_by(DailyProductionEntry.date.asc())
            .all()
        )

        for row in production_rows:
            prod_date = getattr(row, "prod_date", None)
            if not isinstance(prod_date, date):
                continue

            qty_ton = _quantize(_decimal_from_value(getattr(row, "briquette_tons", None)), TON_QUANT)
            if qty_ton <= zero:
                continue

            unit_cost_per_kg = mix_unit_cost_map.get(prod_date, zero)
            unit_cost_per_ton = _quantize(unit_cost_per_kg * TON_TO_KG, CURRENCY_QUANT)

            briquette_receipts_by_date[prod_date].append(
                TonInventoryLayer(quantity_ton=qty_ton, unit_cost_per_ton=unit_cost_per_ton)
            )

        issues_by_date: Dict[date, Decimal] = defaultdict(lambda: zero)

        issue_rows = (
            SalesActualEntry.query.filter(
                SalesActualEntry.date > STOCK_BASE_DATE,
                SalesActualEntry.date <= normalized_date,
            )
            .order_by(SalesActualEntry.date.asc(), SalesActualEntry.id.asc())
            .all()
        )

        for row in issue_rows:
            sale_date = getattr(row, "date", None)
            if not isinstance(sale_date, date):
                continue
            qty = _quantize(_decimal_from_value(getattr(row, "quantity_tons", None)), TON_QUANT)
            if qty > zero:
                issues_by_date[sale_date] = _quantize(issues_by_date[sale_date] + qty, TON_QUANT)

        briquette_dates = sorted(set(briquette_receipts_by_date.keys()) | set(issues_by_date.keys()))
        for day in briquette_dates:
            if day < normalized_date:
                for layer in briquette_receipts_by_date.get(day, []):
                    briquette_layers.append(layer)
                issue_qty = issues_by_date.get(day, zero)
                if issue_qty > zero:
                    _consume_ton_layers(briquette_layers, issue_qty)
                briquette_opening_balance = _sum_layers(briquette_layers)[0]
                continue

            if day == normalized_date:
                briquette_opening_balance = _sum_layers(briquette_layers)[0]
                for layer in briquette_receipts_by_date.get(day, []):
                    briquette_layers.append(layer)
                    briquette_production = _quantize(
                        briquette_production + layer.quantity_ton,
                        TON_QUANT,
                    )
                issue_qty = issues_by_date.get(day, zero)
                if issue_qty > zero:
                    briquette_sales = _quantize(briquette_sales + issue_qty, TON_QUANT)
                    _consume_ton_layers(briquette_layers, issue_qty)
                break

            if day > normalized_date:
                break

    items: List[Dict[str, object]] = []

    for key in MATERIAL_ORDER:
        qty, value = _sum_layers(raw_inventory[key])
        if qty < zero:
            qty = zero
        if value < zero:
            value = zero
        unit_cost: Optional[Decimal]
        if qty > zero:
            unit_cost = _quantize(value / qty, CURRENCY_QUANT)
        else:
            unit_cost = None
        opening_qty = _quantize(material_opening_balances.get(key, zero), TON_QUANT)
        purchases_qty = _quantize(material_purchases.get(key, zero), TON_QUANT)
        consumption_qty = _quantize(material_consumption.get(key, zero), TON_QUANT)
        total_available_qty = _quantize(opening_qty + purchases_qty, TON_QUANT)
        items.append(
            {
                "key": key,
                "label": STOCK_STATUS_LABELS.get(key, key.replace("_", " ").title()),
                "quantity_ton": float(qty),
                "unit_cost_per_ton": float(unit_cost) if unit_cost is not None else None,
                "value_rs": float(value),
                "metrics": {
                    "opening_balance": float(opening_qty),
                    "purchases": float(purchases_qty),
                    "production": None,
                    "sales": None,
                    "consumption": float(consumption_qty),
                    "closing_balance": float(qty),
                    "total_available": float(total_available_qty),
                },
            }
        )

    briquette_qty, briquette_value = _sum_layers(briquette_layers)
    if briquette_qty < zero:
        briquette_qty = zero
    if briquette_value < zero:
        briquette_value = zero
    briquette_unit_cost: Optional[Decimal]
    if briquette_qty > zero:
        briquette_unit_cost = _quantize(briquette_value / briquette_qty, CURRENCY_QUANT)
    else:
        briquette_unit_cost = None

    briquette_opening_qty = _quantize(briquette_opening_balance, TON_QUANT)
    briquette_production_qty = _quantize(briquette_production, TON_QUANT)
    briquette_sales_qty = _quantize(briquette_sales, TON_QUANT)
    briquette_total_available = _quantize(
        briquette_opening_qty + briquette_production_qty,
        TON_QUANT,
    )

    items.append(
        {
            "key": "briquettes",
            "label": STOCK_STATUS_LABELS.get("briquettes", "Briquettes"),
            "quantity_ton": float(briquette_qty),
            "unit_cost_per_ton": float(briquette_unit_cost)
            if briquette_unit_cost is not None
            else None,
            "value_rs": float(briquette_value),
            "metrics": {
                "opening_balance": float(briquette_opening_qty),
                "purchases": 0.0,
                "production": float(briquette_production_qty),
                "sales": float(briquette_sales_qty),
                "consumption": 0.0,
                "closing_balance": float(briquette_qty),
                "total_available": float(briquette_total_available),
            },
        }
    )

    opening_qty_total, opening_value_total = opening_totals
    opening_qty_total = _quantize(opening_qty_total, TON_QUANT)
    opening_value_total = _quantize(opening_value_total, CURRENCY_QUANT)
    opening_unit_cost: Optional[Decimal]
    if opening_qty_total > zero:
        opening_unit_cost = _quantize(opening_value_total / opening_qty_total, CURRENCY_QUANT)
    else:
        opening_unit_cost = None

    items.append(
        {
            "key": "opening_total",
            "label": STOCK_STATUS_LABELS.get("opening_total", "Opening stock"),
            "quantity_ton": float(opening_qty_total),
            "unit_cost_per_ton": float(opening_unit_cost)
            if opening_unit_cost is not None
            else None,
            "value_rs": float(opening_value_total),
        }
    )

    return {"as_of": normalized_date.isoformat(), "items": items}


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
    else:
        missing_dates = [entry.date for entry in entries if entry.date not in production_map]
        if missing_dates:
            extra_map = _load_production_for_dates(missing_dates)
            production_map = {**production_map, **extra_map}

    inventory = _build_inventory_from_opening()
    last_unit_cost = _initial_last_unit_costs()
    receipts = _load_receipt_layers()

    pending_receipts: Dict[str, Deque[ReceiptLayer]] = {key: deque(value) for key, value in receipts.items()}

    for entry in entries:
        entry_breakdown = _ensure_breakdown_structure()
        production = production_map.get(entry.date, {"briquette_tons": Decimal("0"), "dryer_tons": Decimal("0"), "dryer_hours": Decimal("0")})
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

        dry_factor_value = DEFAULT_DRY_FACTOR
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
    dryer_hours = _quantize(production.get("dryer_hours", Decimal("0")), HOUR_QUANT)
    output_kg = _quantize(briquette_tons * TON_TO_KG, KG_QUANT)

    dry_factor = DEFAULT_DRY_FACTOR
    if entry and entry.dry_factor is not None:
        dry_factor = _decimal_from_value(entry.dry_factor)

    if entry and entry.sawdust_qty_ton is not None:
        sawdust_value = _decimal_from_value(entry.sawdust_qty_ton)
    elif dry_factor > 0:
        sawdust_value = dryer_tons / dry_factor
    else:
        sawdust_value = Decimal("0")

    sawdust_ton = _quantize(sawdust_value, TON_QUANT)

    materials: Dict[str, float] = {}
    material_decimals: Dict[str, Decimal] = {}
    for key, attr in ENTRY_FIELD_MAP.items():
        value = _decimal_from_value(getattr(entry, attr, "0")) if entry else Decimal("0")
        quantized = _quantize(value, TON_QUANT)
        material_decimals[key] = quantized
        materials[key] = float(quantized)

    wood_shaving_value = material_decimals.get("wood_shaving", Decimal("0"))
    wood_powder_value = material_decimals.get("wood_powder", Decimal("0"))
    peanut_husk_value = material_decimals.get("peanut_husk", Decimal("0"))
    fire_cut_value = material_decimals.get("fire_cut", Decimal("0"))
    dry_material_value = _quantize(
        dryer_tons + wood_shaving_value + wood_powder_value + peanut_husk_value + fire_cut_value,
        TON_QUANT,
    )

    total_cost = _decimal_from_value(entry.total_material_cost) if entry else Decimal("0")
    unit_cost = _decimal_from_value(entry.unit_cost_per_kg) if entry else Decimal("0")
    breakdown = _serialize_cost_breakdown(entry.cost_breakdown if entry else None)

    return {
        "date": target_date.isoformat(),
        "briquette_production_ton": float(briquette_tons),
        "dryer_production_ton": float(dryer_tons),
        "dryer_actual_running_hours": float(dryer_hours),
        "briquette_output_kg": float(output_kg),
        "dry_factor": float(_quantize(dry_factor, UNIT_COST_QUANT)),
        "materials": materials,
        "sawdust_ton": float(sawdust_ton),
        "wood_shaving_ton": float(material_decimals.get("wood_shaving", Decimal("0"))),
        "dry_material_ton": float(dry_material_value),
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
        {"briquette_tons": Decimal("0"), "dryer_tons": Decimal("0"), "dryer_hours": Decimal("0")},
    )
    return _serialize_mix_entry(target_date, entry, production)


def _validate_stock_levels(target_date: date, consumption_today: Dict[str, Decimal]) -> None:
    if not isinstance(target_date, date):
        raise TypeError("target_date must be a date instance")

    zero = Decimal("0")
    normalized_date = target_date

    opening_layers, _ = _build_opening_layers_ton()
    raw_inventory = _clone_ton_layers(opening_layers)

    receipts_by_date: Dict[date, List[Tuple[str, TonInventoryLayer]]] = defaultdict(list)
    receipt_layers = _load_receipt_layers()
    for key, layers in receipt_layers.items():
        if key not in raw_inventory:
            continue
        for layer in layers:
            layer_date = getattr(layer, "date", None)
            if not isinstance(layer_date, date) or layer_date > normalized_date:
                continue

            qty_kg = _decimal_from_value(getattr(layer, "quantity_kg", None))
            if qty_kg <= zero:
                continue

            qty_ton = _quantize(qty_kg / TON_TO_KG, TON_QUANT)
            if qty_ton <= zero:
                continue

            unit_cost_per_ton = _quantize(
                _decimal_from_value(getattr(layer, "unit_cost", None)) * TON_TO_KG,
                CURRENCY_QUANT,
            )

            receipts_by_date[layer_date].append(
                (key, TonInventoryLayer(quantity_ton=qty_ton, unit_cost_per_ton=unit_cost_per_ton))
            )

    consumption_by_date: Dict[date, Dict[str, Decimal]] = defaultdict(dict)
    if normalized_date > STOCK_BASE_DATE:
        mix_entries = (
            BriquetteMixEntry.query.filter(
                BriquetteMixEntry.date > STOCK_BASE_DATE,
                BriquetteMixEntry.date <= normalized_date,
            )
            .order_by(BriquetteMixEntry.date.asc())
            .all()
        )

        for entry in mix_entries:
            entry_date = getattr(entry, "date", None)
            if not isinstance(entry_date, date) or entry_date == normalized_date:
                continue

            entry_consumption = consumption_by_date[entry_date]
            for key, attr in ENTRY_FIELD_MAP.items():
                if key not in raw_inventory:
                    continue
                quantity = _quantize(_decimal_from_value(getattr(entry, attr, None)), TON_QUANT)
                if quantity > zero:
                    entry_consumption[key] = _quantize(
                        entry_consumption.get(key, zero) + quantity,
                        TON_QUANT,
                    )

    today_consumption: Dict[str, Decimal] = {}
    for key, quantity in (consumption_today or {}).items():
        if key not in raw_inventory:
            continue
        numeric = _quantize(_decimal_from_value(quantity), TON_QUANT)
        if numeric > zero:
            today_consumption[key] = numeric
    consumption_by_date[normalized_date] = today_consumption

    timeline_dates = sorted(
        {day for day in receipts_by_date.keys() | consumption_by_date.keys() if day <= normalized_date}
    )
    if not timeline_dates or timeline_dates[-1] != normalized_date:
        timeline_dates = sorted(set(timeline_dates) | {normalized_date})

    for day in timeline_dates:
        for key, layer in receipts_by_date.get(day, []):
            raw_inventory[key].append(layer)

        day_consumption = consumption_by_date.get(day, {})
        if day == normalized_date:
            for key, quantity in day_consumption.items():
                if quantity <= zero:
                    continue
                available_qty, _ = _sum_layers(raw_inventory[key])
                if quantity > available_qty:
                    label = MATERIAL_LABELS.get(key, key.replace("_", " ").title())
                    raise ValueError(
                        f"Insufficient stock for {label}. Entry not saved. System does not allow negative stock."
                    )

        for key, quantity in day_consumption.items():
            if quantity <= zero:
                continue
            _consume_ton_layers(raw_inventory[key], quantity)


def update_briquette_mix(target_date: date, payload: Dict[str, object]) -> Dict[str, object]:
    payload = payload or {}

    def _parse_positive_decimal(field: str, *, places: Decimal = TON_QUANT) -> Decimal:
        value = payload.get(field)
        numeric = _decimal_from_value(value)
        if numeric < 0:
            raise ValueError(f"{field} cannot be negative.")
        return _quantize(numeric, places)

    dry_factor = _parse_positive_decimal("dry_factor", places=UNIT_COST_QUANT)
    wood_powder = _parse_positive_decimal("wood_powder_ton")
    peanut_husk = _parse_positive_decimal("peanut_husk_ton")
    fire_cut = _parse_positive_decimal("fire_cut_ton")

    production_map = _load_production_for_dates([target_date])
    production = production_map.get(
        target_date,
        {"briquette_tons": Decimal("0"), "dryer_tons": Decimal("0"), "dryer_hours": Decimal("0")},
    )
    dryer_tons = _quantize(production.get("dryer_tons", Decimal("0")), TON_QUANT)
    total_output = _quantize(production.get("briquette_tons", Decimal("0")), TON_QUANT)

    if dry_factor > 0:
        sawdust = _quantize(dryer_tons / dry_factor, TON_QUANT)
    else:
        sawdust = _quantize(Decimal("0"), TON_QUANT)

    if total_output < dryer_tons + wood_powder + peanut_husk + fire_cut:
        raise ValueError(
            "Invalid mix: Wood shaving quantity cannot be negative. Please check inputs."
        )

    wood_shaving = _quantize(
        max(total_output - dryer_tons - wood_powder - peanut_husk - fire_cut, Decimal("0")),
        TON_QUANT,
    )

    consumption_today = {
        "sawdust": sawdust,
        "wood_shaving": wood_shaving,
        "wood_powder": wood_powder,
        "peanut_husk": peanut_husk,
        "fire_cut": fire_cut,
    }
    _validate_stock_levels(target_date, consumption_today)

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
        {"briquette_tons": Decimal("0"), "dryer_tons": Decimal("0"), "dryer_hours": Decimal("0")},
    )
    return _serialize_mix_entry(target_date, entry, production_after)
