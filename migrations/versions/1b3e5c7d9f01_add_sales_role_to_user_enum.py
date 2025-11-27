"""Add sales role to roleenum

Revision ID: 1b3e5c7d9f01
Revises: ('d2b4cf5a3c11', 'f12345abcde0')
Create Date: 2025-11-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1b3e5c7d9f01"
down_revision = ("d2b4cf5a3c11", "f12345abcde0")
branch_labels = None
depends_on = None

ROLE_ENUM_NAME = "roleenum"
SALES_ROLE_VALUE = "sales"
FALLBACK_ROLE_VALUE = "production_manager"
USER_TABLE = sa.table(
    "user",
    sa.column("id", sa.Integer()),
    sa.column("role", sa.String(length=64)),
)


def upgrade():
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    f"ALTER TYPE {ROLE_ENUM_NAME} ADD VALUE IF NOT EXISTS :value"
                ).bindparams(value=SALES_ROLE_VALUE)
            )


def downgrade():
    bind = op.get_bind()

    # Revert any sales roles to the fallback before downgrading schemas that
    # do not support the sales value. Removing enum values in PostgreSQL is not
    # trivial, so we only normalize data here.
    bind.execute(
        sa.update(USER_TABLE)
        .where(USER_TABLE.c.role == SALES_ROLE_VALUE)
        .values(role=FALLBACK_ROLE_VALUE)
    )
