from decimal import Decimal

from routes.team import _count_attendance_days, _resolve_sales_based_target_allowance


def test_sales_based_target_allowance_returns_mid_tier_amount():
    allowance = _resolve_sales_based_target_allowance(
        Decimal("449.91"), Decimal("452.80")
    )

    assert allowance == Decimal("25000")


def test_sales_based_target_allowance_returns_top_tier_amount():
    allowance = _resolve_sales_based_target_allowance(
        Decimal("550.25"), Decimal("560.50")
    )

    assert allowance == Decimal("35000")


def test_sales_based_target_allowance_returns_zero_outside_thresholds():
    allowance = _resolve_sales_based_target_allowance(
        Decimal("430"), Decimal("455")
    )

    assert allowance == Decimal("0")


def test_sales_based_target_allowance_tolerates_float_rounding_errors():
    allowance = _resolve_sales_based_target_allowance(
        Decimal("448.999999"), Decimal("498.999999")
    )

    assert allowance == Decimal("25000")


def test_count_attendance_days_includes_work_day_status_without_times():
    entries = {
        "2025-10-01": {"dayStatus": "Work Day"},
        "2025-10-02": {"onTime": "08:00", "offTime": "17:00"},
        "2025-10-03": {"dayStatus": "Annual Leave"},
        "2025-09-30": {"dayStatus": "Work Day"},
        123: {"dayStatus": "Work Day"},
    }

    assert _count_attendance_days(entries, month="2025-10") == 2
