"""Add sales manager role to roleenum

Revision ID: 7c2f5b7a4d90
Revises: 6af3c7b8e2d1
Create Date: 2026-01-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7c2f5b7a4d90"
down_revision = "6af3c7b8e2d1"
branch_labels = None
depends_on = None

ROLE_ENUM_NAME = "roleenum"
SALES_MANAGER_ROLE_VALUE = "sales_manager"


def upgrade():
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    f"ALTER TYPE {ROLE_ENUM_NAME} ADD VALUE IF NOT EXISTS :value"
                ).bindparams(value=SALES_MANAGER_ROLE_VALUE)
            )


def downgrade():
    # Removing enum values is not straightforward across dialects.
    # No-op downgrade mirrors existing role migrations.
    pass
