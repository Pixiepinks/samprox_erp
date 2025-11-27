"""Add odometer fields to petty cash weekly claims

Revision ID: d6c3f9d0e7ab
Revises: 1b3e5c7d9f01
Create Date: 2025-05-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d6c3f9d0e7ab"
down_revision = "1b3e5c7d9f01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "petty_cash_weekly_claims",
        sa.Column("monday_morning_odo", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "petty_cash_weekly_claims",
        sa.Column("friday_evening_odo", sa.Numeric(14, 2), nullable=True),
    )


def downgrade():
    op.drop_column("petty_cash_weekly_claims", "friday_evening_odo")
    op.drop_column("petty_cash_weekly_claims", "monday_morning_odo")
