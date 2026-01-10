"""Add Exsol sales receipts and invoice status.

Revision ID: c9e1f2a3b4c5
Revises: f1c2d3e4f6a7
Create Date: 2026-05-12 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9e1f2a3b4c5"
down_revision = "f1c2d3e4f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exsol_sales_invoices", sa.Column("status", sa.String(length=30), nullable=True))
    op.create_index(
        "ix_exsol_sales_invoices_company_customer",
        "exsol_sales_invoices",
        ["company_key", "customer_id"],
    )

    op.create_table(
        "exsol_sales_receipts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("company_key", sa.String(length=20), nullable=False, server_default="EXSOL"),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("receipt_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("method", sa.String(length=40), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["invoice_id"], ["exsol_sales_invoices.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_exsol_sales_receipts_invoice_date",
        "exsol_sales_receipts",
        ["invoice_id", "receipt_date"],
    )

    op.create_index(
        "ix_non_samprox_customers_company_name",
        "non_samprox_customers",
        ["company_id", "customer_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_non_samprox_customers_company_name", table_name="non_samprox_customers")
    op.drop_index("ix_exsol_sales_receipts_invoice_date", table_name="exsol_sales_receipts")
    op.drop_table("exsol_sales_receipts")
    op.drop_index("ix_exsol_sales_invoices_company_customer", table_name="exsol_sales_invoices")
    op.drop_column("exsol_sales_invoices", "status")
