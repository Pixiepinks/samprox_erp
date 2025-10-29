"""Seed Rainbow Holdings admin user.

Revision ID: 1c8f4a74e9b8
Revises: 0f87e1b7d6a2
Create Date: 2024-12-05 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from werkzeug.security import generate_password_hash

# revision identifiers, used by Alembic.
revision = "1c8f4a74e9b8"
down_revision = "0f87e1b7d6a2"
branch_labels = None
depends_on = None

user_table = sa.table(
    "user",
    sa.column("id", sa.Integer()),
    sa.column("name", sa.String(length=120)),
    sa.column("email", sa.String(length=120)),
    sa.column("password_hash", sa.String(length=256)),
    sa.column("role", sa.String(length=64)),
    sa.column("active", sa.Boolean()),
)

RAINBOW_ADMIN_EMAIL = "uresha@rainbowsholdings.com"
RAINBOW_ADMIN_PASSWORD = "123"
RAINBOW_ADMIN_NAME = "Uresha"


def upgrade():
    bind = op.get_bind()
    normalized_email = RAINBOW_ADMIN_EMAIL.strip().lower()

    existing = bind.execute(
        sa.select(sa.literal(1))
        .select_from(user_table)
        .where(sa.func.lower(user_table.c.email) == normalized_email)
    ).scalar()

    if existing is None:
        bind.execute(
            user_table.insert().values(
                name=RAINBOW_ADMIN_NAME,
                email=normalized_email,
                password_hash=generate_password_hash(RAINBOW_ADMIN_PASSWORD),
                role="admin",
                active=True,
            )
        )


def downgrade():
    bind = op.get_bind()
    normalized_email = RAINBOW_ADMIN_EMAIL.strip().lower()
    bind.execute(
        user_table.delete().where(sa.func.lower(user_table.c.email) == normalized_email)
    )
