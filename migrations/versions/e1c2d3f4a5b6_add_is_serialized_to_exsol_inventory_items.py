"""Add is_serialized to Exsol inventory items

Revision ID: e1c2d3f4a5b6
Revises: d4e5f6a7b8c9
Create Date: 2026-02-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e1c2d3f4a5b6"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


PRESSURE_CONTROL_NAME = "PRESSURE CONTROL 220-240V 1.5bar"
PRESSURE_CONTROL_PATTERN = "%PRESSURE CONTROL%220-240V%1.5bar%"


def upgrade() -> None:
    op.add_column(
        "exsol_inventory_items",
        sa.Column("is_serialized", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.execute(sa.text("UPDATE exsol_inventory_items SET is_serialized = true"))
    op.execute(
        sa.text(
            "UPDATE exsol_inventory_items SET is_serialized = false "
            "WHERE item_name = :name OR item_name ILIKE :pattern"
        ).bindparams(name=PRESSURE_CONTROL_NAME, pattern=PRESSURE_CONTROL_PATTERN)
    )
    op.execute(
        sa.text(
            "UPDATE exsol_inventory_items SET is_serialized = true WHERE UPPER(item_name) LIKE '%PUMP%'"
        )
    )
    op.alter_column("exsol_inventory_items", "is_serialized", server_default=None)


def downgrade() -> None:
    op.drop_column("exsol_inventory_items", "is_serialized")
