"""add financial statement models

Revision ID: 4d2c0fe5c123
Revises: 0a3b2c1d4e5f
Create Date: 2024-06-15 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4d2c0fe5c123"
down_revision = "0a3b2c1d4e5f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "financial_statement_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("statement_type", sa.String(length=50), nullable=False),
        sa.Column("line_key", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_section", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_subtotal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_calculated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("statement_type", "line_key", name="uq_statement_line_key"),
    )

    op.create_table(
        "financial_statement_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("statement_type", sa.String(length=50), nullable=False),
        sa.Column("line_key", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "company_id",
            "year",
            "month",
            "statement_type",
            "line_key",
            name="uq_financial_statement_value",
        ),
    )

    statement_lines_table = sa.table(
        "financial_statement_lines",
        sa.column("statement_type", sa.String()),
        sa.column("line_key", sa.String()),
        sa.column("label", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("level", sa.Integer()),
        sa.column("is_section", sa.Boolean()),
        sa.column("is_subtotal", sa.Boolean()),
        sa.column("is_calculated", sa.Boolean()),
    )

    income_lines = [
        {"line_key": "revenue", "label": "Revenue", "display_order": 10, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "sales_returns", "label": "Less: Sales Returns & Discounts", "display_order": 20, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "cogs_section", "label": "Cost of Sales", "display_order": 30, "level": 0, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "opening_inventory", "label": "  Opening Inventory", "display_order": 40, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "purchases", "label": "  Purchases", "display_order": 50, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "direct_expenses", "label": "  Direct Expenses", "display_order": 60, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "closing_inventory", "label": "  Less: Closing Inventory", "display_order": 70, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "cogs_total", "label": "Total Cost of Goods Sold", "display_order": 80, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "gross_profit", "label": "Gross Profit", "display_order": 90, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "other_income_header", "label": "Other Income", "display_order": 100, "level": 0, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "interest_income", "label": "  Interest Income", "display_order": 110, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "rental_income", "label": "  Rental Income", "display_order": 120, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "other_operating_income", "label": "  Other Operating Income", "display_order": 130, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "operating_expenses_header", "label": "Operating Expenses", "display_order": 140, "level": 0, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "admin_expenses", "label": "  Administrative Expenses", "display_order": 150, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "selling_expenses", "label": "  Selling & Distribution Expenses", "display_order": 160, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "other_operating_expenses", "label": "  Other Operating Expenses", "display_order": 170, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "operating_profit", "label": "Operating Profit", "display_order": 180, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "finance_costs_header", "label": "Finance Costs", "display_order": 190, "level": 0, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "interest_expense", "label": "  Interest Expense", "display_order": 200, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "profit_before_tax", "label": "Profit Before Tax", "display_order": 210, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "income_tax_expense", "label": "Income Tax Expense", "display_order": 220, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "profit_for_period", "label": "Profit for the Period", "display_order": 230, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
    ]

    sofp_lines = [
        {"line_key": "assets_header", "label": "Assets", "display_order": 10, "level": 0, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "non_current_assets_header", "label": "Non-Current Assets", "display_order": 20, "level": 1, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "ppe", "label": "  Property, Plant and Equipment", "display_order": 30, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "intangibles", "label": "  Intangible Assets", "display_order": 40, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "investments_lt", "label": "  Long-term Investments", "display_order": 50, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "deferred_tax_asset", "label": "  Deferred Tax Asset", "display_order": 60, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "total_non_current_assets", "label": "Total Non-Current Assets", "display_order": 70, "level": 1, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "current_assets_header", "label": "Current Assets", "display_order": 80, "level": 1, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "inventory", "label": "  Inventory", "display_order": 90, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "trade_receivables", "label": "  Trade Receivables", "display_order": 100, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "other_receivables", "label": "  Other Receivables", "display_order": 110, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "advances", "label": "  Advances and Deposits", "display_order": 120, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "cash_and_bank", "label": "  Cash & Cash Equivalents", "display_order": 130, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "total_current_assets", "label": "Total Current Assets", "display_order": 140, "level": 1, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "total_assets", "label": "Total Assets", "display_order": 150, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "equity_liabilities_header", "label": "Equity and Liabilities", "display_order": 160, "level": 0, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "equity_header", "label": "Equity", "display_order": 170, "level": 1, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "share_capital", "label": "  Share Capital", "display_order": 180, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "retained_earnings", "label": "  Retained Earnings", "display_order": 190, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "other_reserves", "label": "  Other Reserves", "display_order": 200, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "total_equity", "label": "Total Equity", "display_order": 210, "level": 1, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "non_current_liabilities_header", "label": "Non-Current Liabilities", "display_order": 220, "level": 1, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "long_term_loans", "label": "  Long-Term Loans", "display_order": 230, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "lease_liabilities_noncurrent", "label": "  Lease Liabilities", "display_order": 240, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "deferred_tax_liability", "label": "  Deferred Tax Liabilities", "display_order": 250, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "total_non_current_liabilities", "label": "Total Non-Current Liabilities", "display_order": 260, "level": 1, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "current_liabilities_header", "label": "Current Liabilities", "display_order": 270, "level": 1, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "trade_payables", "label": "  Trade Payables", "display_order": 280, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "accruals", "label": "  Accruals and Other Payables", "display_order": 290, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "short_term_loans", "label": "  Short-Term Loans", "display_order": 300, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "bank_overdraft", "label": "  Bank Overdraft", "display_order": 310, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "taxes_payable", "label": "  Taxes Payable", "display_order": 320, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "total_current_liabilities", "label": "Total Current Liabilities", "display_order": 330, "level": 1, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "total_equity_and_liabilities", "label": "Total Equity and Liabilities", "display_order": 340, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
    ]

    cashflow_lines = [
        {"line_key": "operating_header", "label": "Cash flows from Operating Activities", "display_order": 10, "level": 0, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "profit_before_tax", "label": "  Profit Before Tax", "display_order": 20, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "depreciation", "label": "  Depreciation and Amortisation", "display_order": 30, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "finance_costs", "label": "  Finance Costs", "display_order": 40, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "other_adjustments", "label": "  Other Non-Cash Adjustments", "display_order": 50, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "change_in_receivables", "label": "  (Increase)/Decrease in Receivables", "display_order": 60, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "change_in_inventory", "label": "  (Increase)/Decrease in Inventory", "display_order": 70, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "change_in_payables", "label": "  Increase/(Decrease) in Payables", "display_order": 80, "level": 2, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "cash_generated_operations", "label": "Cash Generated from Operations", "display_order": 90, "level": 1, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "income_tax_paid", "label": "Income Tax Paid", "display_order": 100, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "net_cash_from_operating", "label": "Net Cash from Operating Activities", "display_order": 110, "level": 1, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "investing_header", "label": "Cash flows from Investing Activities", "display_order": 120, "level": 0, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "purchase_ppe", "label": "  Purchase of PPE", "display_order": 130, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "proceeds_sale_ppe", "label": "  Proceeds from Sale of PPE", "display_order": 140, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "purchase_investments", "label": "  Purchase of Investments", "display_order": 150, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "investment_income", "label": "  Investment Income", "display_order": 160, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "net_cash_from_investing", "label": "Net Cash from Investing Activities", "display_order": 170, "level": 1, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "financing_header", "label": "Cash flows from Financing Activities", "display_order": 180, "level": 0, "is_section": True, "is_subtotal": False, "is_calculated": False},
        {"line_key": "proceeds_share_issue", "label": "  Proceeds from Share Issue", "display_order": 190, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "loans_received", "label": "  Loans Received", "display_order": 200, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "loan_repayments", "label": "  Loan Repayments", "display_order": 210, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "lease_payments", "label": "  Lease Payments", "display_order": 220, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "dividends_paid", "label": "  Dividends Paid", "display_order": 230, "level": 1, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "net_cash_from_financing", "label": "Net Cash from Financing Activities", "display_order": 240, "level": 1, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "net_increase_cash", "label": "Net Increase/(Decrease) in Cash & Cash Equivalents", "display_order": 250, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "opening_cash", "label": "Opening Cash & Cash Equivalents", "display_order": 260, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "closing_cash", "label": "Closing Cash & Cash Equivalents", "display_order": 270, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
    ]

    equity_lines = [
        {"line_key": "opening_share_capital", "label": "Opening Balance – Share Capital", "display_order": 10, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "opening_retained_earnings", "label": "Opening Balance – Retained Earnings", "display_order": 20, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "opening_other_reserves", "label": "Opening Balance – Other Reserves", "display_order": 30, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "profit_for_period", "label": "Profit for the Period", "display_order": 40, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "other_comprehensive_income", "label": "Other Comprehensive Income", "display_order": 50, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "dividends", "label": "Dividends", "display_order": 60, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "new_share_capital", "label": "New Share Capital Issued", "display_order": 70, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "transfers_between_reserves", "label": "Transfers Between Reserves", "display_order": 80, "level": 0, "is_section": False, "is_subtotal": False, "is_calculated": False},
        {"line_key": "closing_share_capital", "label": "Closing Balance – Share Capital", "display_order": 90, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "closing_retained_earnings", "label": "Closing Balance – Retained Earnings", "display_order": 100, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
        {"line_key": "closing_other_reserves", "label": "Closing Balance – Other Reserves", "display_order": 110, "level": 0, "is_section": False, "is_subtotal": True, "is_calculated": True},
    ]

    def with_statement(statement_type: str, items: list[dict]) -> list[dict]:
        return [dict(item, statement_type=statement_type) for item in items]

    op.bulk_insert(
        statement_lines_table,
        with_statement("income", income_lines)
        + with_statement("sofp", sofp_lines)
        + with_statement("cashflow", cashflow_lines)
        + with_statement("equity", equity_lines),
    )


def downgrade():
    op.drop_table("financial_statement_values")
    op.drop_table("financial_statement_lines")
    op.drop_table("companies")
