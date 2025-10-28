"""Add pay category column to team members.

Revision ID: 0f87e1b7d6a2
Revises: c40f6b5f1de8
Create Date: 2024-11-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

revision = "0f87e1b7d6a2"
down_revision = "c40f6b5f1de8"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade():
    columns = _table_columns("team_member")

    if "pay_category" not in columns:
        with op.batch_alter_table("team_member") as batch_op:
            batch_op.add_column(sa.Column("pay_category", sa.String(length=50), nullable=True))

    op.execute(
        text(
            """
            UPDATE team_member
            SET pay_category = CASE
                WHEN pay_category IS NULL THEN 'Office'
                WHEN LOWER(TRIM(pay_category)) IN ('office', '') THEN 'Office'
                WHEN LOWER(TRIM(pay_category)) = 'factory' THEN 'Factory'
                WHEN LOWER(TRIM(pay_category)) = 'casual' THEN 'Casual'
                WHEN LOWER(TRIM(pay_category)) = 'other' THEN 'Other'
                ELSE 'Office'
            END
            """
        )
    )

    with op.batch_alter_table("team_member") as batch_op:
        batch_op.alter_column(
            "pay_category",
            existing_type=sa.String(length=50),
            nullable=False,
        )


def downgrade():
    columns = _table_columns("team_member")

    if "pay_category" in columns:
        with op.batch_alter_table("team_member") as batch_op:
            batch_op.drop_column("pay_category")
