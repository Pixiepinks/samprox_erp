"""Merge serial events heads.

Revision ID: 9c4f1a7b2d33
Revises: 1ed3919f16f0, 8a1c4d2f6e90
Create Date: 2026-01-15 00:00:00.000000
"""

from alembic import op

revision = "9c4f1a7b2d33"
down_revision = ("1ed3919f16f0", "8a1c4d2f6e90")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SELECT 1")


def downgrade() -> None:
    op.execute("SELECT 1")
