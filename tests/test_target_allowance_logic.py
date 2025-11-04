from decimal import Decimal


from routes.team import _resolve_sales_based_target_allowance


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
