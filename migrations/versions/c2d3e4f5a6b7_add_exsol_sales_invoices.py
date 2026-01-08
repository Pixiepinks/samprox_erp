"""Add Exsol sales invoice tables.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5f8
Create Date: 2026-02-15 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5f8"
branch_labels = None
depends_on = None


def _schema_name(bind) -> str | None:
    return "exsol_sales" if bind.dialect.name == "postgresql" else None


def upgrade() -> None:
    bind = op.get_bind()
    schema = _schema_name(bind)

    if schema:
        op.execute('CREATE SCHEMA IF NOT EXISTS "exsol_sales"')

    op.add_column(
        "exsol_production_serials",
        sa.Column(
            "is_sold",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "exsol_sales_invoices",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "company_name",
            sa.String(length=255),
            nullable=False,
            server_default="Exsol Engineering (Pvt) Ltd",
        ),
        sa.Column("invoice_no", sa.String(length=60), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=120)),
        sa.Column("district", sa.String(length=120)),
        sa.Column("province", sa.String(length=120)),
        sa.Column("sales_rep_id", sa.Integer(), nullable=False),
        sa.Column("sales_rep_name", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("invoice_no", name="uq_exsol_sales_invoices_invoice_no"),
        sa.Index("ix_exsol_sales_invoices_invoice_date", "invoice_date"),
        schema=schema,
    )

    op.create_table(
        "exsol_sales_invoice_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("invoice_id", sa.BigInteger(), nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=False),
        sa.Column("serial_number", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mrp", sa.Numeric(14, 2), nullable=False),
        sa.Column("discount_rate", sa.Integer(), nullable=False),
        sa.Column("discount_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("dealer_price", sa.Numeric(14, 2), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_exsol_sales_invoice_items_qty_positive"),
        sa.CheckConstraint(
            "discount_rate IN (26, 31)",
            name="ck_exsol_sales_invoice_items_discount_rate",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            [f"{schema + '.' if schema else ''}exsol_sales_invoices.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "serial_number",
            name="uq_exsol_sales_invoice_items_serial_number",
        ),
        sa.Index("ix_exsol_sales_invoice_items_invoice_id", "invoice_id"),
        schema=schema,
    )


def downgrade() -> None:
    bind = op.get_bind()
    schema = _schema_name(bind)

    op.drop_table("exsol_sales_invoice_items", schema=schema)
    op.drop_table("exsol_sales_invoices", schema=schema)
    op.drop_column("exsol_production_serials", "is_sold")

    if schema:
        op.execute('DROP SCHEMA IF EXISTS "exsol_sales"')
