"""add finance manager role

Revision ID: c1f5a5d8c4b2
Revises: 4e89b6b28b1a
Create Date: 2025-10-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import func
from werkzeug.security import generate_password_hash


# revision identifiers, used by Alembic.
revision = "c1f5a5d8c4b2"
down_revision = "4e89b6b28b1a"
branch_labels = None
depends_on = None


ROLE_ENUM_NAME = "roleenum"
FINANCE_ROLE_VALUE = "finance_manager"
FINANCE_EMAIL = "finance@samprox.lk"
FINANCE_NAME = "Finance Manager"
FINANCE_PASSWORD = "123"

user_table = sa.table(
    "user",
    sa.column("id", sa.Integer()),
    sa.column("name", sa.String(length=120)),
    sa.column("email", sa.String(length=120)),
    sa.column("password_hash", sa.String(length=256)),
    sa.column("role", sa.String(length=64)),
    sa.column("active", sa.Boolean()),
)


def upgrade():
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # Adding a new enum value in PostgreSQL requires the statement to run
        # outside of the surrounding transaction; otherwise PostgreSQL will
        # raise "unsafe use of new value" when the value is used later in the
        # same transaction. Running it inside an autocommit block makes sure the
        # enum alteration is committed before we insert records that reference
        # the new value.
        with op.get_context().autocommit_block():
            op.execute(
                sa.text(
                    f"ALTER TYPE {ROLE_ENUM_NAME} ADD VALUE IF NOT EXISTS :value"
                ).bindparams(value=FINANCE_ROLE_VALUE)
            )

    existing = bind.execute(
        sa.select(sa.literal(1))
        .select_from(user_table)
        .where(func.lower(user_table.c.email) == FINANCE_EMAIL.lower())
    ).scalar()

    if existing is None:
        bind.execute(
            user_table.insert().values(
                name=FINANCE_NAME,
                email=FINANCE_EMAIL,
                password_hash=generate_password_hash(FINANCE_PASSWORD),
                role=FINANCE_ROLE_VALUE,
                active=True,
            )
        )


def downgrade():
    bind = op.get_bind()
    bind.execute(
        user_table.delete().where(func.lower(user_table.c.email) == FINANCE_EMAIL.lower())
    )
