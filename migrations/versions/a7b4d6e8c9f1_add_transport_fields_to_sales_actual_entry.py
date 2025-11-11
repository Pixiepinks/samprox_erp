"""Add transport fields to sales actual entry

Revision ID: a7b4d6e8c9f1
Revises: dabf19e2c3a4
Create Date: 2025-02-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a7b4d6e8c9f1"
down_revision = "dabf19e2c3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_actual_entry",
        sa.Column("vehicle_number", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "sales_actual_entry",
        sa.Column("driver_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sales_actual_entry",
        sa.Column("helper1_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sales_actual_entry",
        sa.Column("helper2_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sales_actual_entry",
        sa.Column("mileage_km", sa.Float(), nullable=True),
    )

    op.create_foreign_key(
        "fk_sales_actual_entry_driver",
        "sales_actual_entry",
        "team_member",
        ["driver_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_sales_actual_entry_helper1",
        "sales_actual_entry",
        "team_member",
        ["helper1_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_sales_actual_entry_helper2",
        "sales_actual_entry",
        "team_member",
        ["helper2_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_sales_actual_entry_helper2", "sales_actual_entry", type_="foreignkey")
    op.drop_constraint("fk_sales_actual_entry_helper1", "sales_actual_entry", type_="foreignkey")
    op.drop_constraint("fk_sales_actual_entry_driver", "sales_actual_entry", type_="foreignkey")

    op.drop_column("sales_actual_entry", "mileage_km")
    op.drop_column("sales_actual_entry", "helper2_id")
    op.drop_column("sales_actual_entry", "helper1_id")
    op.drop_column("sales_actual_entry", "driver_id")
    op.drop_column("sales_actual_entry", "vehicle_number")
