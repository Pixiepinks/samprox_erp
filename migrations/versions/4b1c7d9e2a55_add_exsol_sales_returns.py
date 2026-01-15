"""Add Exsol sales returns tables and serial status.

Revision ID: 4b1c7d9e2a55
Revises: 3f8d1e2c5b67
Create Date: 2024-06-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "4b1c7d9e2a55"
down_revision = "3f8d1e2c5b67"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "exsol_sales_invoice_serials",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="SOLD"),
    )
    op.execute(
        "UPDATE exsol_sales_invoice_serials SET status = 'SOLD' WHERE status IS NULL"
    )

    op.create_table(
        "exsol_sales_returns",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("company_key", sa.String(length=20), nullable=False, server_default="EXSOL"),
        sa.Column("return_no", sa.String(length=60), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("customer_id", sa.String(length=36), nullable=False),
        sa.Column("return_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("reason", sa.String(length=255)),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("created_by_user_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["exsol_sales_invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["non_samprox_customers.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"]),
        sa.UniqueConstraint(
            "company_id",
            "return_no",
            name="uq_exsol_sales_returns_company_return_no",
        ),
    )
    op.create_index(
        "ix_exsol_sales_returns_company_return_no",
        "exsol_sales_returns",
        ["company_id", "return_no"],
    )
    op.create_index(
        "ix_exsol_sales_returns_invoice_id",
        "exsol_sales_returns",
        ["invoice_id"],
    )
    op.create_index(
        "ix_exsol_sales_returns_company_id",
        "exsol_sales_returns",
        ["company_id"],
    )
    op.create_index(
        "ix_exsol_sales_returns_company_key",
        "exsol_sales_returns",
        ["company_key"],
    )

    op.create_table(
        "exsol_sales_return_lines",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("return_id", sa.String(length=36), nullable=False),
        sa.Column("item_code", sa.String(length=50), nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("is_serialized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(
            ["return_id"],
            ["exsol_sales_returns.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_exsol_sales_return_lines_return_id",
        "exsol_sales_return_lines",
        ["return_id"],
    )

    op.create_table(
        "exsol_sales_return_serials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("return_line_id", sa.String(length=36), nullable=False),
        sa.Column("serial_number", sa.String(length=60), nullable=False),
        sa.Column("condition", sa.String(length=20), nullable=False, server_default="GOOD"),
        sa.Column("restock_status", sa.String(length=20), nullable=False, server_default="STORED"),
        sa.ForeignKeyConstraint(
            ["return_line_id"],
            ["exsol_sales_return_lines.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_exsol_sales_return_serials_return_line_id",
        "exsol_sales_return_serials",
        ["return_line_id"],
    )
    op.create_index(
        "ix_exsol_sales_return_serials_serial_number",
        "exsol_sales_return_serials",
        ["serial_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_exsol_sales_return_serials_serial_number", table_name="exsol_sales_return_serials")
    op.drop_index("ix_exsol_sales_return_serials_return_line_id", table_name="exsol_sales_return_serials")
    op.drop_table("exsol_sales_return_serials")
    op.drop_index("ix_exsol_sales_return_lines_return_id", table_name="exsol_sales_return_lines")
    op.drop_table("exsol_sales_return_lines")
    op.drop_index("ix_exsol_sales_returns_company_key", table_name="exsol_sales_returns")
    op.drop_index("ix_exsol_sales_returns_company_id", table_name="exsol_sales_returns")
    op.drop_index("ix_exsol_sales_returns_invoice_id", table_name="exsol_sales_returns")
    op.drop_index("ix_exsol_sales_returns_company_return_no", table_name="exsol_sales_returns")
    op.drop_table("exsol_sales_returns")
    op.drop_column("exsol_sales_invoice_serials", "status")
