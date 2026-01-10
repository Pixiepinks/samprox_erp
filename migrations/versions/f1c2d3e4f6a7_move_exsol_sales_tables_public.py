"""Move Exsol sales invoice tables to public schema.

Revision ID: f1c2d3e4f6a7
Revises: f0b1c2d3e4f5
Create Date: 2026-04-20 00:00:01.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1c2d3e4f6a7"
down_revision = "f0b1c2d3e4f5"
branch_labels = None
depends_on = None


def _ensure_tables_in_public(bind) -> None:
    inspector = sa.inspect(bind)
    tables = [
        "exsol_sales_invoices",
        "exsol_sales_invoice_lines",
        "exsol_sales_invoice_serials",
    ]

    if bind.dialect.name == "postgresql":
        schema_names = inspector.get_schema_names()
        if "exsol_sales" in schema_names:
            for table in tables:
                if inspector.has_table(table, schema="exsol_sales") and not inspector.has_table(
                    table, schema="public"
                ):
                    op.execute(f'ALTER TABLE "exsol_sales"."{table}" SET SCHEMA public')

    for table in tables:
        if inspector.has_table(table, schema=None):
            continue
        if table == "exsol_sales_invoices":
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
            )
        elif table == "exsol_sales_invoice_lines":
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
                    ["exsol_sales_invoices.id"],
                    ondelete="CASCADE",
                ),
                sa.ForeignKeyConstraint(
                    ["item_id"],
                    ["exsol_inventory_items.id"],
                ),
                sa.Index("ix_exsol_sales_invoice_lines_invoice_id", "invoice_id"),
            )
        elif table == "exsol_sales_invoice_serials":
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
                    ["exsol_sales_invoices.id"],
                    ondelete="CASCADE",
                ),
                sa.ForeignKeyConstraint(
                    ["line_id"],
                    ["exsol_sales_invoice_lines.id"],
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
            )


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_tables_in_public(bind)


def downgrade() -> None:
    op.drop_table("exsol_sales_invoice_serials")
    op.drop_table("exsol_sales_invoice_lines")
    op.drop_table("exsol_sales_invoices")
