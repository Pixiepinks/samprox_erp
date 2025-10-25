"""Add secondary idle reason column to machine idle events

Revision ID: c67fd8d0f1a4
Revises: 1f6b49872f4b
Create Date: 2024-02-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c67fd8d0f1a4"
down_revision = "1f6b49872f4b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "machine_idle_event",
        sa.Column("secondary_reason", sa.String(length=255), nullable=True),
    )


def downgrade():
    op.drop_column("machine_idle_event", "secondary_reason")

