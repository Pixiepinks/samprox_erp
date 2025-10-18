"""add quantity columns to sales entries

Revision ID: b2b3c026bbf0
Revises: 3a72c2b8dd4f
Create Date: 2024-06-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b2b3c026bbf0"
down_revision = "3a72c2b8dd4f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sales_forecast_entry",
        sa.Column("unit_price", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sales_forecast_entry",
        sa.Column("quantity_tons", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sales_actual_entry",
        sa.Column("unit_price", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sales_actual_entry",
        sa.Column("quantity_tons", sa.Float(), nullable=False, server_default="0"),
    )

    op.alter_column("sales_forecast_entry", "unit_price", server_default=None)
    op.alter_column("sales_forecast_entry", "quantity_tons", server_default=None)
    op.alter_column("sales_actual_entry", "unit_price", server_default=None)
    op.alter_column("sales_actual_entry", "quantity_tons", server_default=None)


def downgrade():
    op.drop_column("sales_actual_entry", "quantity_tons")
    op.drop_column("sales_actual_entry", "unit_price")
    op.drop_column("sales_forecast_entry", "quantity_tons")
    op.drop_column("sales_forecast_entry", "unit_price")
