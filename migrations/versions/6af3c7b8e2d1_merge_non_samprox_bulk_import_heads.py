"""Merge heads for dealer bulk import

Revision ID: 6af3c7b8e2d1
Revises: 1c2a4f7b8d90, 2e3f4b5c6d70
Create Date: 2026-03-21 00:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "6af3c7b8e2d1"
down_revision = ("1c2a4f7b8d90", "2e3f4b5c6d70")
branch_labels = None
depends_on = None


def upgrade() -> None:  # pragma: no cover - metadata only
    pass


def downgrade() -> None:  # pragma: no cover - metadata only
    pass
