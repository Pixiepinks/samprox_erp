"""
Add chart of accounts master table

Revision ID: ba5b85c5c1df
Revises: 1aa9c6fb6b6a
Create Date: 2025-07-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ba5b85c5c1df"
down_revision = "1aa9c6fb6b6a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chart_of_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("account_code", sa.String(length=20), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=False),
        sa.Column("ifrs_category", sa.String(length=50), nullable=False),
        sa.Column("ifrs_subcategory", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("company_id", "account_code", name="uq_chart_account_company_code"),
    )

    account_table = sa.table(
        "chart_of_accounts",
        sa.column("company_id", sa.Integer()),
        sa.column("account_code", sa.String(length=20)),
        sa.column("account_name", sa.String(length=255)),
        sa.column("ifrs_category", sa.String(length=50)),
        sa.column("ifrs_subcategory", sa.String(length=100)),
    )

    op.bulk_insert(
        account_table,
        [
            # ASSETS – Current
            {"company_id": None, "account_code": "1100", "account_name": "Cash & Cash Equivalents", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1110", "account_name": "Petty Cash", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1120", "account_name": "Bank – Current Account", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1130", "account_name": "Bank – Savings Account", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1140", "account_name": "Short-Term Fixed Deposits", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1200", "account_name": "Trade Receivables", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1210", "account_name": "Allowance for Doubtful Debts", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1300", "account_name": "Inventory – Raw Materials", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1310", "account_name": "Inventory – Work in Progress", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1320", "account_name": "Inventory – Finished Goods", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1330", "account_name": "Inventory – Spares & Consumables", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1400", "account_name": "Prepayments", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1410", "account_name": "Advances to Suppliers", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            {"company_id": None, "account_code": "1420", "account_name": "Other Current Assets", "ifrs_category": "Asset", "ifrs_subcategory": "Current Asset"},
            # ASSETS – Non-current
            {"company_id": None, "account_code": "1500", "account_name": "Property, Plant & Equipment", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1501", "account_name": "Land", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1502", "account_name": "Buildings", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1503", "account_name": "Machinery", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1504", "account_name": "Furniture & Equipment", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1505", "account_name": "Motor Vehicles", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1510", "account_name": "Accumulated Depreciation – Buildings", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1511", "account_name": "Accumulated Depreciation – Machinery", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1512", "account_name": "Accumulated Depreciation – Vehicles", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1600", "account_name": "Intangible Assets", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1610", "account_name": "Software", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1620", "account_name": "Goodwill", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1700", "account_name": "Deferred Tax Asset", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            {"company_id": None, "account_code": "1800", "account_name": "Long-Term Deposits / Guarantees", "ifrs_category": "Asset", "ifrs_subcategory": "Non-current Asset"},
            # LIABILITIES – Current
            {"company_id": None, "account_code": "2100", "account_name": "Trade Payables", "ifrs_category": "Liability", "ifrs_subcategory": "Current Liability"},
            {"company_id": None, "account_code": "2110", "account_name": "Accrued Expenses", "ifrs_category": "Liability", "ifrs_subcategory": "Current Liability"},
            {"company_id": None, "account_code": "2120", "account_name": "Wages Payable", "ifrs_category": "Liability", "ifrs_subcategory": "Current Liability"},
            {"company_id": None, "account_code": "2130", "account_name": "Statutory Deductions Payable (EPF/ETF/PAYE)", "ifrs_category": "Liability", "ifrs_subcategory": "Current Liability"},
            {"company_id": None, "account_code": "2140", "account_name": "VAT Payable", "ifrs_category": "Liability", "ifrs_subcategory": "Current Liability"},
            {"company_id": None, "account_code": "2150", "account_name": "Other Taxes Payable", "ifrs_category": "Liability", "ifrs_subcategory": "Current Liability"},
            {"company_id": None, "account_code": "2200", "account_name": "Bank Overdraft", "ifrs_category": "Liability", "ifrs_subcategory": "Current Liability"},
            {"company_id": None, "account_code": "2300", "account_name": "Short-Term Loans", "ifrs_category": "Liability", "ifrs_subcategory": "Current Liability"},
            {"company_id": None, "account_code": "2400", "account_name": "Customer Advances", "ifrs_category": "Liability", "ifrs_subcategory": "Current Liability"},
            # LIABILITIES – Non-current
            {"company_id": None, "account_code": "2500", "account_name": "Long-Term Bank Loan", "ifrs_category": "Liability", "ifrs_subcategory": "Non-current Liability"},
            {"company_id": None, "account_code": "2510", "account_name": "Lease Liability", "ifrs_category": "Liability", "ifrs_subcategory": "Non-current Liability"},
            {"company_id": None, "account_code": "2600", "account_name": "Deferred Tax Liability", "ifrs_category": "Liability", "ifrs_subcategory": "Non-current Liability"},
            {"company_id": None, "account_code": "2700", "account_name": "Employee Benefit Obligations", "ifrs_category": "Liability", "ifrs_subcategory": "Non-current Liability"},
            {"company_id": None, "account_code": "2800", "account_name": "Other Long-Term Liabilities", "ifrs_category": "Liability", "ifrs_subcategory": "Non-current Liability"},
            # EQUITY
            {"company_id": None, "account_code": "3000", "account_name": "Stated Capital", "ifrs_category": "Equity", "ifrs_subcategory": "Share Capital"},
            {"company_id": None, "account_code": "3100", "account_name": "Share Premium", "ifrs_category": "Equity", "ifrs_subcategory": "Share Premium"},
            {"company_id": None, "account_code": "3200", "account_name": "Retained Earnings", "ifrs_category": "Equity", "ifrs_subcategory": "Retained Earnings"},
            {"company_id": None, "account_code": "3300", "account_name": "Revaluation Reserves", "ifrs_category": "Equity", "ifrs_subcategory": "Other Reserves"},
            {"company_id": None, "account_code": "3400", "account_name": "Other Reserves", "ifrs_category": "Equity", "ifrs_subcategory": "Other Reserves"},
            {"company_id": None, "account_code": "3500", "account_name": "Dividend Payable", "ifrs_category": "Equity", "ifrs_subcategory": "Other Reserves"},
            # INCOME – Operating
            {"company_id": None, "account_code": "4100", "account_name": "Sales – Local", "ifrs_category": "Income", "ifrs_subcategory": "Operating Revenue"},
            {"company_id": None, "account_code": "4110", "account_name": "Sales – Export", "ifrs_category": "Income", "ifrs_subcategory": "Operating Revenue"},
            {"company_id": None, "account_code": "4120", "account_name": "Other Operating Income", "ifrs_category": "Income", "ifrs_subcategory": "Operating Revenue"},
            {"company_id": None, "account_code": "4200", "account_name": "Sales Returns & Discounts", "ifrs_category": "Income", "ifrs_subcategory": "Operating Revenue"},
            # INCOME – Other
            {"company_id": None, "account_code": "4300", "account_name": "Interest Income", "ifrs_category": "Income", "ifrs_subcategory": "Other Income"},
            {"company_id": None, "account_code": "4400", "account_name": "Gain on Sale of Assets", "ifrs_category": "Income", "ifrs_subcategory": "Other Income"},
            {"company_id": None, "account_code": "4500", "account_name": "Other Income", "ifrs_category": "Income", "ifrs_subcategory": "Other Income"},
            # EXPENSES – Cost of Sales
            {"company_id": None, "account_code": "5100", "account_name": "Opening Inventory", "ifrs_category": "Expense", "ifrs_subcategory": "Cost of Sales"},
            {"company_id": None, "account_code": "5110", "account_name": "Purchases", "ifrs_category": "Expense", "ifrs_subcategory": "Cost of Sales"},
            {"company_id": None, "account_code": "5120", "account_name": "Direct Labour", "ifrs_category": "Expense", "ifrs_subcategory": "Cost of Sales"},
            {"company_id": None, "account_code": "5130", "account_name": "Direct Expense", "ifrs_category": "Expense", "ifrs_subcategory": "Cost of Sales"},
            {"company_id": None, "account_code": "5140", "account_name": "Freight / Clearing", "ifrs_category": "Expense", "ifrs_subcategory": "Cost of Sales"},
            {"company_id": None, "account_code": "5150", "account_name": "Closing Inventory", "ifrs_category": "Expense", "ifrs_subcategory": "Cost of Sales"},
            # EXPENSES – Distribution
            {"company_id": None, "account_code": "5300", "account_name": "Transportation Expense", "ifrs_category": "Expense", "ifrs_subcategory": "Distribution Expense"},
            {"company_id": None, "account_code": "5310", "account_name": "Sales Commission", "ifrs_category": "Expense", "ifrs_subcategory": "Distribution Expense"},
            {"company_id": None, "account_code": "5320", "account_name": "Advertising & Promotion", "ifrs_category": "Expense", "ifrs_subcategory": "Distribution Expense"},
            # EXPENSES – Administrative
            {"company_id": None, "account_code": "5400", "account_name": "Salaries & Wages", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5410", "account_name": "EPF / ETF Employer Contribution", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5420", "account_name": "Office Rent", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5430", "account_name": "Utilities", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5440", "account_name": "Office Supplies", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5450", "account_name": "Insurance", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5460", "account_name": "Repairs & Maintenance", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5470", "account_name": "Fuel & Vehicle Expenses", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5480", "account_name": "Travelling", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5490", "account_name": "IT & Internet", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5500", "account_name": "Professional Fees", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5510", "account_name": "Audit Fees", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5520", "account_name": "Bank Charges", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            {"company_id": None, "account_code": "5600", "account_name": "Depreciation Expense", "ifrs_category": "Expense", "ifrs_subcategory": "Administrative Expense"},
            # EXPENSES – Finance & Tax
            {"company_id": None, "account_code": "5800", "account_name": "Interest Expense – Bank", "ifrs_category": "Expense", "ifrs_subcategory": "Finance Cost"},
            {"company_id": None, "account_code": "5810", "account_name": "Lease Interest", "ifrs_category": "Expense", "ifrs_subcategory": "Finance Cost"},
            {"company_id": None, "account_code": "5900", "account_name": "Income Tax Expense", "ifrs_category": "Expense", "ifrs_subcategory": "Tax Expense"},
            # OTHER COMPREHENSIVE INCOME (OCI)
            {"company_id": None, "account_code": "6100", "account_name": "OCI – Revaluation Gain", "ifrs_category": "OCI", "ifrs_subcategory": "Other Comprehensive Income – Gain"},
            {"company_id": None, "account_code": "6200", "account_name": "OCI – Revaluation Loss", "ifrs_category": "OCI", "ifrs_subcategory": "Other Comprehensive Income – Loss"},
            {"company_id": None, "account_code": "6300", "account_name": "OCI – Actuarial Gain", "ifrs_category": "OCI", "ifrs_subcategory": "Other Comprehensive Income – Gain"},
            {"company_id": None, "account_code": "6400", "account_name": "OCI – Actuarial Loss", "ifrs_category": "OCI", "ifrs_subcategory": "Other Comprehensive Income – Loss"},
        ],
    )


def downgrade():
    op.drop_table("chart_of_accounts")
