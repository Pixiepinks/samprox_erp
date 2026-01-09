"""Add Exsol invoice lines table and company key.

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-03-05 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "d4e5f6a7b8c9"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def _schema_name(bind) -> str | None:
    return "exsol_sales" if bind.dialect.name == "postgresql" else None


def upgrade() -> None:
    bind = op.get_bind()
    schema = _schema_name(bind)

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("exsol_sales_invoices") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "company_key",
                    sa.String(length=20),
                    nullable=False,
                    server_default="EXSOL",
                )
            )
            batch_op.drop_constraint("uq_exsol_sales_invoices_invoice_no", type_="unique")
            batch_op.create_unique_constraint(
                "uq_exsol_sales_invoices_company_invoice_no",
                ["company_key", "invoice_no"],
            )
    else:
        op.add_column(
            "exsol_sales_invoices",
            sa.Column(
                "company_key",
                sa.String(length=20),
                nullable=False,
                server_default="EXSOL",
            ),
            schema=schema,
        )
        op.drop_constraint(
            "uq_exsol_sales_invoices_invoice_no",
            "exsol_sales_invoices",
            type_="unique",
            schema=schema,
        )
        op.create_unique_constraint(
            "uq_exsol_sales_invoices_company_invoice_no",
            "exsol_sales_invoices",
            ["company_key", "invoice_no"],
            schema=schema,
        )

    serials_default = sa.text("'[]'::jsonb") if bind.dialect.name == "postgresql" else sa.text("'[]'")
    op.create_table(
        "exsol_sales_invoice_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("invoice_id", sa.BigInteger(), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("mrp", sa.Numeric(14, 2), nullable=False),
        sa.Column("trade_discount_rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("discount_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("dealer_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("serials_json", sa.JSON(), nullable=False, server_default=serials_default),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("quantity > 0", name="ck_exsol_sales_invoice_lines_qty_positive"),
        sa.CheckConstraint(
            "trade_discount_rate IN (0.26, 0.31)",
            name="ck_exsol_sales_invoice_lines_discount_rate",
        ),
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


def downgrade() -> None:
    bind = op.get_bind()
    schema = _schema_name(bind)

    op.drop_table("exsol_sales_invoice_lines", schema=schema)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("exsol_sales_invoices") as batch_op:
            batch_op.drop_constraint(
                "uq_exsol_sales_invoices_company_invoice_no",
                type_="unique",
            )
            batch_op.create_unique_constraint(
                "uq_exsol_sales_invoices_invoice_no",
                ["invoice_no"],
            )
            batch_op.drop_column("company_key")
    else:
        op.drop_constraint(
            "uq_exsol_sales_invoices_company_invoice_no",
            "exsol_sales_invoices",
            type_="unique",
            schema=schema,
        )
        op.create_unique_constraint(
            "uq_exsol_sales_invoices_invoice_no",
            "exsol_sales_invoices",
            ["invoice_no"],
            schema=schema,
        )
        op.drop_column("exsol_sales_invoices", "company_key", schema=schema)
