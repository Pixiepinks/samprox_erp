"""Utilities for responsibility performance value conversions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from models import ResponsibilityPerformanceUnit


_EPOCH = date(1970, 1, 1)


@dataclass(frozen=True)
class PerformanceUnitConfig:
    """Configuration for how a performance unit behaves."""

    input_type: str
    decimal_places: int = 4


_UNIT_CONFIG: dict[ResponsibilityPerformanceUnit, PerformanceUnitConfig] = {
    ResponsibilityPerformanceUnit.DATE: PerformanceUnitConfig("date", decimal_places=0),
    ResponsibilityPerformanceUnit.TIME: PerformanceUnitConfig("time", decimal_places=0),
    ResponsibilityPerformanceUnit.HOURS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.MINUTES: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.DAYS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.WEEKS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.MONTHS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.YEARS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.QUANTITY_BASED: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.QTY: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.UNITS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.PIECES: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.BATCHES: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.ITEMS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.PARCELS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.ORDERS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.AMOUNT_LKR: PerformanceUnitConfig("currency"),
    ResponsibilityPerformanceUnit.REVENUE: PerformanceUnitConfig("currency"),
    ResponsibilityPerformanceUnit.COST: PerformanceUnitConfig("currency"),
    ResponsibilityPerformanceUnit.EXPENSE: PerformanceUnitConfig("currency"),
    ResponsibilityPerformanceUnit.PROFIT: PerformanceUnitConfig("currency"),
    ResponsibilityPerformanceUnit.SAVINGS: PerformanceUnitConfig("currency"),
    ResponsibilityPerformanceUnit.MARGIN_PCT: PerformanceUnitConfig("percentage"),
    ResponsibilityPerformanceUnit.NUMBER: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.COUNT: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.SCORE: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.FREQUENCY: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.RATE: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.INDEX: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.KG: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.TONNES: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.LITRES: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.METERS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.KWH: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.RPM: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.QUALITY_METRIC: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.PERCENTAGE_PCT: PerformanceUnitConfig("percentage"),
    ResponsibilityPerformanceUnit.ERROR_RATE_PCT: PerformanceUnitConfig("percentage"),
    ResponsibilityPerformanceUnit.SUCCESS_RATE_PCT: PerformanceUnitConfig("percentage"),
    ResponsibilityPerformanceUnit.DEFECTS_PER_UNIT: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.ACCURACY_PCT: PerformanceUnitConfig("percentage"),
    ResponsibilityPerformanceUnit.COMPLIANCE_PCT: PerformanceUnitConfig("percentage"),
    ResponsibilityPerformanceUnit.TIME_PER_UNIT: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.UNITS_PER_HOUR: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.CYCLE_TIME: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.LEAD_TIME: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.CUSTOMER_COUNT: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.LEADS: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.CONVERSION_PCT: PerformanceUnitConfig("percentage"),
    ResponsibilityPerformanceUnit.TICKETS_RESOLVED: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.RESPONSE_TIME: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.MILESTONES: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.STAGES: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.COMPLETION_PCT: PerformanceUnitConfig("percentage"),
    ResponsibilityPerformanceUnit.TASKS_COMPLETED: PerformanceUnitConfig("decimal"),
    ResponsibilityPerformanceUnit.SLA_PCT: PerformanceUnitConfig("percentage"),
}


def get_unit_config(unit: ResponsibilityPerformanceUnit | None) -> PerformanceUnitConfig:
    """Return configuration for ``unit`` falling back to decimal inputs."""

    if isinstance(unit, ResponsibilityPerformanceUnit):
        return _UNIT_CONFIG.get(unit, PerformanceUnitConfig("decimal"))
    return PerformanceUnitConfig("decimal")


def unit_input_type(unit: ResponsibilityPerformanceUnit | None) -> str:
    """Return the preferred input type for the provided unit."""

    return get_unit_config(unit).input_type


def _quantize(value: Decimal, places: int) -> Decimal:
    quantize_exp = Decimal(1).scaleb(-places)
    return value.quantize(quantize_exp, rounding=ROUND_HALF_UP)


def _parse_decimal(value: str, places: int = 4) -> Decimal:
    decimal_value = Decimal(value)
    return _quantize(decimal_value, places)


def parse_performance_value(
    unit: ResponsibilityPerformanceUnit,
    value: Optional[str],
) -> Optional[Decimal]:
    """Convert a user-supplied value into a decimal stored representation."""

    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    config = get_unit_config(unit)

    if unit is ResponsibilityPerformanceUnit.DATE:
        try:
            parsed_date = date.fromisoformat(stripped)
        except ValueError as exc:  # pragma: no cover - marshmallow handles messaging
            raise ValueError("Invalid date value.") from exc
        days = (parsed_date - _EPOCH).days
        return _quantize(Decimal(days), config.decimal_places)

    if unit is ResponsibilityPerformanceUnit.TIME:
        parts = stripped.split(":")
        if len(parts) < 2:
            raise ValueError("Time must be in HH:MM format.")
        try:
            hours = int(parts[0])
            minutes = int(parts[1])
        except ValueError as exc:
            raise ValueError("Time must be in HH:MM format.") from exc
        total_minutes = hours * 60 + minutes
        return _quantize(Decimal(total_minutes), config.decimal_places)

    return _parse_decimal(stripped, config.decimal_places)


def format_performance_value(
    unit: ResponsibilityPerformanceUnit,
    value: Optional[Decimal],
) -> Optional[str]:
    """Return a string suitable for UI presentation."""

    if value is None:
        return None

    config = get_unit_config(unit)

    if unit is ResponsibilityPerformanceUnit.DATE:
        try:
            days = int(value)
        except (TypeError, ValueError):
            return None
        target_date = _EPOCH + timedelta(days=days)
        return target_date.isoformat()

    if unit is ResponsibilityPerformanceUnit.TIME:
        try:
            total_minutes = int(value)
        except (TypeError, ValueError):
            return None
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours:02d}:{minutes:02d}"

    normalized = value.normalize()
    string_value = format(normalized, "f")

    if config.decimal_places == 0:
        return string_value.split(".")[0]

    if "." in string_value:
        string_value = string_value.rstrip("0").rstrip(".")
    return string_value


def calculate_metric(
    unit: ResponsibilityPerformanceUnit,
    responsible: Decimal,
    actual: Optional[Decimal],
) -> Optional[Decimal]:
    """Calculate the performance metric as actual minus responsible."""

    if actual is None:
        return None

    difference = actual - responsible

    if unit in {ResponsibilityPerformanceUnit.DATE, ResponsibilityPerformanceUnit.TIME}:
        return difference

    return _quantize(difference, 1)


def format_metric(
    unit: ResponsibilityPerformanceUnit,
    metric: Optional[Decimal],
) -> Optional[str]:
    """Return a display string for the computed metric."""

    if metric is None:
        return None

    if unit is ResponsibilityPerformanceUnit.DATE:
        days = int(metric)
        return str(days)

    if unit is ResponsibilityPerformanceUnit.TIME:
        minutes = int(metric)
        return str(minutes)

    normalized = metric.normalize()
    string_value = format(normalized, "f")
    if "." in string_value:
        string_value = string_value.rstrip("0").rstrip(".")
    return string_value
