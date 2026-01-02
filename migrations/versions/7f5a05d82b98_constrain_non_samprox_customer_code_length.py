"""Constrain Non Samprox customer codes to 6 characters

Revision ID: 7f5a05d82b98
Revises: c8d4f5a12b34
Create Date: 2025-05-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "7f5a05d82b98"
down_revision = "c8d4f5a12b34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("non_samprox_customers") as batch_op:
        batch_op.alter_column(
            "customer_code",
            existing_type=sa.String(length=30),
            type_=sa.String(length=6),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("non_samprox_customers") as batch_op:
        batch_op.alter_column(
            "customer_code",
            existing_type=sa.String(length=6),
            type_=sa.String(length=30),
            nullable=False,
        )
