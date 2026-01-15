"""Merge Exsol sales return heads.

Revision ID: 7b3a9c2d1e0f
Revises: 4b1c7d9e2a55, c9e1f2a3b4c5
Create Date: 2026-01-15 00:00:00.000000
"""

from alembic import op

revision = "7b3a9c2d1e0f"
down_revision = ("4b1c7d9e2a55", "c9e1f2a3b4c5")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SELECT 1")


def downgrade() -> None:
    op.execute("SELECT 1")
