"""add customer and sales tables

Revision ID: 8e1d273b3f5f
Revises: 4e89b6b28b1a
Create Date: 2024-05-18 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8e1d273b3f5f"
down_revision = "4e89b6b28b1a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "sales_forecast_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sales_forecast_entry_customer_id"),
        "sales_forecast_entry",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sales_forecast_entry_date"),
        "sales_forecast_entry",
        ["date"],
        unique=False,
    )

    op.create_table(
        "sales_actual_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sales_actual_entry_customer_id"),
        "sales_actual_entry",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sales_actual_entry_date"),
        "sales_actual_entry",
        ["date"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_sales_actual_entry_date"), table_name="sales_actual_entry")
    op.drop_index(op.f("ix_sales_actual_entry_customer_id"), table_name="sales_actual_entry")
    op.drop_table("sales_actual_entry")

    op.drop_index(op.f("ix_sales_forecast_entry_date"), table_name="sales_forecast_entry")
    op.drop_index(op.f("ix_sales_forecast_entry_customer_id"), table_name="sales_forecast_entry")
    op.drop_table("sales_forecast_entry")

    op.drop_table("customer")
