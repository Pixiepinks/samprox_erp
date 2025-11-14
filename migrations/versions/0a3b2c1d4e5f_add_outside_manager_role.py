"""add outside manager role

Revision ID: 0a3b2c1d4e5f
Revises: f9a2d1c4e5b6
Create Date: 2024-05-29 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import func


# revision identifiers, used by Alembic.
revision = "0a3b2c1d4e5f"
down_revision = "f9a2d1c4e5b6"
branch_labels = None
depends_on = None


ROLE_ENUM_NAME = "roleenum"
OUTSIDE_ROLE_VALUE = "outside_manager"
NIMAL_EMAIL = "nimal@exsol.lk"
DEFAULT_FALLBACK_ROLE = "production_manager"

user_table = sa.table(
    "user",
    sa.column("id", sa.Integer()),
    sa.column("email", sa.String(length=120)),
    sa.column("role", sa.String(length=64)),
)


def upgrade():
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    f"ALTER TYPE {ROLE_ENUM_NAME} ADD VALUE IF NOT EXISTS :value"
                ).bindparams(value=OUTSIDE_ROLE_VALUE)
            )

    bind.execute(
        user_table.update()
        .where(func.lower(user_table.c.email) == NIMAL_EMAIL.lower())
        .values(role=OUTSIDE_ROLE_VALUE)
    )


def downgrade():
    bind = op.get_bind()
    bind.execute(
        user_table.update()
        .where(func.lower(user_table.c.email) == NIMAL_EMAIL.lower())
        .values(role=DEFAULT_FALLBACK_ROLE)
    )
