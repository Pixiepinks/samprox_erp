"""Helpers for responsibility performance metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from models import ResponsibilityPerformanceUnit

MINUTES_IN_DAY = Decimal(24 * 60)
MINUTES_IN_HOUR = Decimal(60)


class PerformanceMetricValidationError(ValueError):
    """Raised when performance metric values are invalid."""


@dataclass(frozen=True)
class PerformanceUnitConfig:
    key: ResponsibilityPerformanceUnit
    label: str
    input_type: str
    allows_negative: bool = False
    decimals: int | None = None
    display_prefix: str | None = None
    display_suffix: str | None = None
    badge_suffix: str | None = None
    base_minutes_factor: Decimal | None = None
    integer_only: bool = False
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    helper_hint: str | None = None


def _decimal_from_value(value: object, field_name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise PerformanceMetricValidationError(f"{field_name} is required.")
        try:
            return Decimal(stripped)
        except Exception as error:  # pragma: no cover - defensive
            raise PerformanceMetricValidationError(
                f"{field_name} must be a valid number."
            ) from error
    raise PerformanceMetricValidationError(
        f"{field_name} must be a number."
    )


def _parse_date(value: object, field_name: str) -> Decimal:
    if isinstance(value, date) and not isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise PerformanceMetricValidationError(
                f"{field_name} must be a valid date (YYYY-MM-DD)."
            ) from error
    else:
        raise PerformanceMetricValidationError(
            f"{field_name} must be a valid date (YYYY-MM-DD)."
        )
    return Decimal(parsed.toordinal()) * MINUTES_IN_DAY


def _parse_time(value: object, field_name: str) -> Decimal:
    if isinstance(value, dt_time):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise PerformanceMetricValidationError(
                f"{field_name} must be a valid time (HH:MM)."
            )
        try:
            parsed = datetime.strptime(stripped, "%H:%M").time()
        except ValueError as error:
            raise PerformanceMetricValidationError(
                f"{field_name} must be a valid time (HH:MM)."
            ) from error
    else:
        raise PerformanceMetricValidationError(
            f"{field_name} must be a valid time (HH:MM)."
        )
    return Decimal(parsed.hour * 60 + parsed.minute)


def _format_date(value: Decimal) -> str:
    minutes = int(value.to_integral_value(rounding=ROUND_HALF_UP))
    ordinal = minutes // (24 * 60)
    return date.fromordinal(ordinal).isoformat()


def _format_time(value: Decimal) -> str:
    total_minutes = int(value.to_integral_value(rounding=ROUND_HALF_UP))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


_ALLOWED_NEGATIVE = {
    ResponsibilityPerformanceUnit.PROFIT,
    ResponsibilityPerformanceUnit.MARGIN_PCT,
    ResponsibilityPerformanceUnit.PERCENTAGE_PCT,
    ResponsibilityPerformanceUnit.ERROR_RATE_PCT,
    ResponsibilityPerformanceUnit.RATE,
    ResponsibilityPerformanceUnit.INDEX,
}

_DURATION_FACTORS = {
    ResponsibilityPerformanceUnit.HOURS: MINUTES_IN_HOUR,
    ResponsibilityPerformanceUnit.MINUTES: Decimal(1),
    ResponsibilityPerformanceUnit.DAYS: MINUTES_IN_DAY,
    ResponsibilityPerformanceUnit.WEEKS: MINUTES_IN_DAY * Decimal(7),
    ResponsibilityPerformanceUnit.MONTHS: MINUTES_IN_DAY * Decimal(30),
    ResponsibilityPerformanceUnit.YEARS: MINUTES_IN_DAY * Decimal(365),
}

_INTEGER_UNITS = {
    ResponsibilityPerformanceUnit.QUANTITY_BASED,
    ResponsibilityPerformanceUnit.QTY,
    ResponsibilityPerformanceUnit.UNITS,
    ResponsibilityPerformanceUnit.PIECES,
    ResponsibilityPerformanceUnit.BATCHES,
    ResponsibilityPerformanceUnit.ITEMS,
    ResponsibilityPerformanceUnit.PARCELS,
    ResponsibilityPerformanceUnit.ORDERS,
    ResponsibilityPerformanceUnit.NUMBER,
    ResponsibilityPerformanceUnit.COUNT,
    ResponsibilityPerformanceUnit.SCORE,
    ResponsibilityPerformanceUnit.FREQUENCY,
    ResponsibilityPerformanceUnit.TICKETS_RESOLVED,
    ResponsibilityPerformanceUnit.CUSTOMER_COUNT,
    ResponsibilityPerformanceUnit.LEADS,
    ResponsibilityPerformanceUnit.TASKS_COMPLETED,
}

_PERCENT_UNITS = {unit for unit in ResponsibilityPerformanceUnit if unit.value.endswith("_pct")}

_CURRENCY_UNITS = {
    ResponsibilityPerformanceUnit.AMOUNT_LKR,
    ResponsibilityPerformanceUnit.REVENUE,
    ResponsibilityPerformanceUnit.COST,
    ResponsibilityPerformanceUnit.EXPENSE,
    ResponsibilityPerformanceUnit.PROFIT,
    ResponsibilityPerformanceUnit.SAVINGS,
}

_PHYSICAL_UNITS = {
    ResponsibilityPerformanceUnit.KG,
    ResponsibilityPerformanceUnit.TONNES,
    ResponsibilityPerformanceUnit.LITRES,
    ResponsibilityPerformanceUnit.METERS,
    ResponsibilityPerformanceUnit.KWH,
    ResponsibilityPerformanceUnit.RPM,
}

_RATE_HINTS = {
    ResponsibilityPerformanceUnit.UNITS_PER_HOUR: "units/hour",
    ResponsibilityPerformanceUnit.TIME_PER_UNIT: "minutes per unit",
    ResponsibilityPerformanceUnit.CYCLE_TIME: "cycle minutes",
    ResponsibilityPerformanceUnit.LEAD_TIME: "lead minutes",
    ResponsibilityPerformanceUnit.RESPONSE_TIME: "response minutes",
}

_LABEL_OVERRIDES = {
    ResponsibilityPerformanceUnit.AMOUNT_LKR: "Amount (LKR)",
    ResponsibilityPerformanceUnit.PERCENTAGE_PCT: "Percentage (%)",
    ResponsibilityPerformanceUnit.ERROR_RATE_PCT: "Error rate (%)",
    ResponsibilityPerformanceUnit.SUCCESS_RATE_PCT: "Success rate (%)",
    ResponsibilityPerformanceUnit.ACCURACY_PCT: "Accuracy (%)",
    ResponsibilityPerformanceUnit.COMPLIANCE_PCT: "Compliance (%)",
    ResponsibilityPerformanceUnit.CONVERSION_PCT: "Conversion (%)",
    ResponsibilityPerformanceUnit.COMPLETION_PCT: "Completion (%)",
    ResponsibilityPerformanceUnit.SLA_PCT: "SLA (%)",
}


def _default_label(key: ResponsibilityPerformanceUnit) -> str:
    value = key.value.replace("_", " ")
    return value.title()


def _unit_suffix(key: ResponsibilityPerformanceUnit) -> str | None:
    if key == ResponsibilityPerformanceUnit.HOURS:
        return "hrs"
    if key == ResponsibilityPerformanceUnit.MINUTES:
        return "min"
    if key == ResponsibilityPerformanceUnit.DAYS:
        return "days"
    if key == ResponsibilityPerformanceUnit.WEEKS:
        return "weeks"
    if key == ResponsibilityPerformanceUnit.MONTHS:
        return "months"
    if key == ResponsibilityPerformanceUnit.YEARS:
        return "years"
    if key in _PERCENT_UNITS:
        return "%"
    if key in _PHYSICAL_UNITS:
        return key.value.upper()
    if key in {
        ResponsibilityPerformanceUnit.UNITS_PER_HOUR,
        ResponsibilityPerformanceUnit.TIME_PER_UNIT,
        ResponsibilityPerformanceUnit.CYCLE_TIME,
        ResponsibilityPerformanceUnit.LEAD_TIME,
        ResponsibilityPerformanceUnit.RESPONSE_TIME,
    }:
        return None
    return None


def build_unit_config(unit: ResponsibilityPerformanceUnit) -> PerformanceUnitConfig:
    label = _LABEL_OVERRIDES.get(unit, _default_label(unit))
    allows_negative = unit in _ALLOWED_NEGATIVE
    input_type = "decimal"
    decimals = 2
    display_prefix = None
    display_suffix = _unit_suffix(unit)
    helper_hint = _RATE_HINTS.get(unit)
    base_minutes_factor = None
    integer_only = unit in _INTEGER_UNITS
    min_value = Decimal("-200") if allows_negative else Decimal("0")
    max_value = None

    if unit == ResponsibilityPerformanceUnit.DATE:
        input_type = "date"
        decimals = None
        display_suffix = None
    elif unit == ResponsibilityPerformanceUnit.TIME:
        input_type = "time"
        decimals = None
        display_suffix = None
    elif unit in _DURATION_FACTORS:
        input_type = "duration"
        decimals = 1
        base_minutes_factor = _DURATION_FACTORS[unit]
    elif unit in _CURRENCY_UNITS:
        input_type = "currency"
        decimals = 2
        display_prefix = "LKR"
    elif unit in _PERCENT_UNITS:
        input_type = "percentage"
        decimals = 1
        max_value = Decimal("200")
    elif unit in _PHYSICAL_UNITS:
        input_type = "decimal"
        if unit in {ResponsibilityPerformanceUnit.KG, ResponsibilityPerformanceUnit.TONNES}:
            decimals = 3
        elif unit in {ResponsibilityPerformanceUnit.LITRES, ResponsibilityPerformanceUnit.METERS}:
            decimals = 2
        elif unit in {ResponsibilityPerformanceUnit.KWH, ResponsibilityPerformanceUnit.RPM}:
            decimals = 2
    elif unit in {
        ResponsibilityPerformanceUnit.MILESTONES,
        ResponsibilityPerformanceUnit.STAGES,
    }:
        input_type = "decimal"
        decimals = 1
    elif integer_only:
        decimals = 0

    if unit in {
        ResponsibilityPerformanceUnit.MARGIN_PCT,
        ResponsibilityPerformanceUnit.PERCENTAGE_PCT,
        ResponsibilityPerformanceUnit.ERROR_RATE_PCT,
    }:
        min_value = Decimal("-200")

    return PerformanceUnitConfig(
        key=unit,
        label=label,
        input_type=input_type,
        allows_negative=allows_negative,
        decimals=decimals,
        display_prefix=display_prefix,
        display_suffix=display_suffix,
        base_minutes_factor=base_minutes_factor,
        integer_only=integer_only,
        min_value=min_value,
        max_value=max_value,
        helper_hint=helper_hint,
    )


_UNIT_CONFIGS = {unit.value: build_unit_config(unit) for unit in ResponsibilityPerformanceUnit}


def get_unit_config(unit_key: str | ResponsibilityPerformanceUnit) -> PerformanceUnitConfig:
    if isinstance(unit_key, ResponsibilityPerformanceUnit):
        key = unit_key.value
    else:
        key = str(unit_key)
    config = _UNIT_CONFIGS.get(key)
    if not config:
        raise PerformanceMetricValidationError("Invalid performance unit of measure.")
    return config


def normalize_performance_value(
    unit_key: str | ResponsibilityPerformanceUnit,
    value: object,
    *,
    field_name: str,
) -> Decimal:
    config = get_unit_config(unit_key)

    if config.input_type == "date":
        normalized = _parse_date(value, field_name)
    elif config.input_type == "time":
        normalized = _parse_time(value, field_name)
    else:
        normalized = _decimal_from_value(value, field_name)
        if config.base_minutes_factor:
            normalized *= config.base_minutes_factor
        if not config.allows_negative and normalized < 0:
            raise PerformanceMetricValidationError(
                f"{field_name} cannot be negative for this unit."
            )
        if config.integer_only and normalized != normalized.to_integral_value():
            raise PerformanceMetricValidationError(
                f"{field_name} must be a whole number."
            )
        if config.min_value is not None and normalized < config.min_value:
            raise PerformanceMetricValidationError(
                f"{field_name} must be at least {config.min_value}."
            )
        if config.max_value is not None and normalized > config.max_value:
            raise PerformanceMetricValidationError(
                f"{field_name} must be at most {config.max_value}."
            )
    return normalized


def compute_metric(
    unit_key: str | ResponsibilityPerformanceUnit,
    responsible_value: Decimal,
    actual_value: Decimal,
) -> Decimal:
    if responsible_value is None or actual_value is None:
        raise PerformanceMetricValidationError("Both values are required to compute metrics.")

    if responsible_value == 0:
        metric = Decimal("0")
    else:
        metric = (actual_value / responsible_value) * Decimal("100")

    metric = metric.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    config = get_unit_config(unit_key)
    lower_bound = Decimal("0")
    upper_bound = Decimal("200")
    if config.allows_negative:
        lower_bound = Decimal("-200")
    if metric < lower_bound:
        metric = lower_bound
    if metric > upper_bound:
        metric = upper_bound
    return metric


def denormalize_value(
    unit_key: str | ResponsibilityPerformanceUnit,
    stored_value: Decimal | None,
) -> object:
    if stored_value is None:
        return None
    config = get_unit_config(unit_key)
    value = Decimal(stored_value)
    if config.input_type == "date":
        return _format_date(value)
    if config.input_type == "time":
        return _format_time(value)
    if config.base_minutes_factor:
        normalized = value / config.base_minutes_factor
    else:
        normalized = value
    if config.integer_only:
        return int(normalized)
    quantize_exp = Decimal(f"1e-{config.decimals}") if config.decimals else None
    if quantize_exp is not None:
        normalized = normalized.quantize(quantize_exp, rounding=ROUND_HALF_UP)
    return float(normalized) if config.decimals and config.decimals > 0 else float(normalized)


def format_value_for_display(unit_key: str | ResponsibilityPerformanceUnit, value: Decimal | None) -> str:
    if value is None:
        return "—"
    config = get_unit_config(unit_key)
    denormalized = denormalize_value(unit_key, value)
    if denormalized is None:
        return "—"
    if config.input_type == "date":
        return str(denormalized)
    if config.input_type == "time":
        return str(denormalized)

    if isinstance(denormalized, float):
        decimals = config.decimals or 0
        formatted = f"{denormalized:.{decimals}f}"
    else:
        formatted = str(denormalized)

    if config.input_type == "currency":
        return f"LKR {Decimal(str(denormalized)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"

    if config.display_suffix:
        return f"{formatted} {config.display_suffix}".strip()
    if config.display_prefix:
        return f"{config.display_prefix} {formatted}".strip()
    return formatted


def list_unit_options() -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    for config in _UNIT_CONFIGS.values():
        decimals = config.decimals
        options.append(
            {
                "key": config.key.value,
                "label": config.label,
                "inputType": config.input_type,
                "allowsNegative": config.allows_negative,
                "decimals": decimals,
                "prefix": config.display_prefix,
                "suffix": config.display_suffix,
                "helperHint": config.helper_hint,
                "baseMinutesFactor": float(config.base_minutes_factor)
                if config.base_minutes_factor
                else None,
                "minValue": float(config.min_value) if config.min_value is not None else None,
                "maxValue": float(config.max_value) if config.max_value is not None else None,
                "integerOnly": config.integer_only,
            }
        )
    options.sort(key=lambda item: item["label"].lower())
    return options
