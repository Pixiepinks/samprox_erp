"""seed admin user

Revision ID: 2f1b3d696a2b
Revises: 54a09e015319
Create Date: 2025-10-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from werkzeug.security import generate_password_hash


# revision identifiers, used by Alembic.
revision = '2f1b3d696a2b'
down_revision = '54a09e015319'
branch_labels = None
depends_on = None


user_table = sa.table(
    'user',
    sa.column('id', sa.Integer()),
    sa.column('name', sa.String(length=120)),
    sa.column('email', sa.String(length=120)),
    sa.column('password_hash', sa.String(length=256)),
    sa.column('role', sa.String(length=64)),
    sa.column('active', sa.Boolean()),
)


ADMIN_EMAIL = "admin@samprox.lk"
ADMIN_PASSWORD = "Admin@123"


def upgrade():
    bind = op.get_bind()

    existing = bind.execute(
        sa.select(sa.literal(1)).select_from(user_table).where(user_table.c.email == ADMIN_EMAIL)
    ).scalar()

    if existing is None:
        bind.execute(
            user_table.insert().values(
                name="System Administrator",
                email=ADMIN_EMAIL,
                password_hash=generate_password_hash(ADMIN_PASSWORD),
                role="admin",
                active=True,
            )
        )


def downgrade():
    bind = op.get_bind()
    bind.execute(
        user_table.delete().where(user_table.c.email == ADMIN_EMAIL)
    )
