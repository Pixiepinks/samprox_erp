"""Create Exsol sales invoice tables.

Revision ID: f0b1c2d3e4f5
Revises: e1c2d3f4a5b6
Create Date: 2026-04-20 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f0b1c2d3e4f5"
down_revision = "e1c2d3f4a5b6"
branch_labels = None
depends_on = None


def _schema_name(bind) -> str | None:
    return "exsol_sales" if bind.dialect.name == "postgresql" else None


def upgrade() -> None:
    bind = op.get_bind()
    schema = _schema_name(bind)
    inspector = sa.inspect(bind)

    if schema:
        op.execute('CREATE SCHEMA IF NOT EXISTS "exsol_sales"')

    def table_exists(table_name: str) -> bool:
        return inspector.has_table(table_name, schema=schema)

    if table_exists("exsol_sales_invoice_serials"):
        op.drop_table("exsol_sales_invoice_serials", schema=schema)
    if table_exists("exsol_sales_invoice_lines"):
        op.drop_table("exsol_sales_invoice_lines", schema=schema)
    if table_exists("exsol_sales_invoice_items"):
        op.drop_table("exsol_sales_invoice_items", schema=schema)
    if table_exists("exsol_sales_invoices"):
        op.drop_table("exsol_sales_invoices", schema=schema)

    op.create_table(
        "exsol_sales_invoices",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "company_key",
            sa.String(length=20),
            nullable=False,
            server_default="EXSOL",
        ),
        sa.Column("invoice_no", sa.String(length=60), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("customer_id", sa.String(length=36), nullable=False),
        sa.Column("sales_rep_id", sa.Integer(), nullable=False),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("discount_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("grand_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["customer_id"], ["non_samprox_customers.id"]),
        sa.UniqueConstraint(
            "company_key",
            "invoice_no",
            name="uq_exsol_sales_invoices_company_invoice_no",
        ),
        sa.Index("ix_exsol_sales_invoices_company_invoice_no", "company_key", "invoice_no"),
        sa.Index("ix_exsol_sales_invoices_company_invoice_date", "company_key", "invoice_date"),
        schema=schema,
    )

    op.create_table(
        "exsol_sales_invoice_lines",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "company_key",
            sa.String(length=20),
            nullable=False,
            server_default="EXSOL",
        ),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("mrp", sa.Numeric(14, 2)),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("discount_rate", sa.Numeric(6, 4)),
        sa.Column("discount_value", sa.Numeric(14, 2)),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("qty > 0", name="ck_exsol_sales_invoice_lines_qty_positive"),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            [f"{schema + '.' if schema else ''}exsol_sales_invoices.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["exsol_inventory_items.id"],
        ),
        sa.Index("ix_exsol_sales_invoice_lines_invoice_id", "invoice_id"),
        schema=schema,
    )

    op.create_table(
        "exsol_sales_invoice_serials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "company_key",
            sa.String(length=20),
            nullable=False,
            server_default="EXSOL",
        ),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("line_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("serial_no", sa.String(length=60), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            [f"{schema + '.' if schema else ''}exsol_sales_invoices.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["line_id"],
            [f"{schema + '.' if schema else ''}exsol_sales_invoice_lines.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["exsol_inventory_items.id"],
        ),
        sa.UniqueConstraint(
            "company_key",
            "item_id",
            "serial_no",
            name="uq_exsol_sales_invoice_serial_company_item_serial",
        ),
        schema=schema,
    )


def downgrade() -> None:
    bind = op.get_bind()
    schema = _schema_name(bind)

    op.drop_table("exsol_sales_invoice_serials", schema=schema)
    op.drop_table("exsol_sales_invoice_lines", schema=schema)
    op.drop_table("exsol_sales_invoices", schema=schema)
