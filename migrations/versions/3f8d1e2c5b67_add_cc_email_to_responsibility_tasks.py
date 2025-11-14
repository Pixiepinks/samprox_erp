"""add cc_email to responsibility tasks

Revision ID: 3f8d1e2c5b67
Revises: fb3e3d7c9a1b
Create Date: 2024-06-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3f8d1e2c5b67"
down_revision = "fb3e3d7c9a1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "responsibility_task",
        sa.Column("cc_email", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("responsibility_task", "cc_email")
