"""add team work calendar table

Revision ID: 9a8d14272867
Revises: f73970f46c90
Create Date: 2024-07-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func


# revision identifiers, used by Alembic.
revision = "9a8d14272867"
down_revision = "f73970f46c90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "team_work_calendar_day",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("is_work_day", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("holiday_name", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=func.now()),
    )
    op.create_index(
        "ix_team_work_calendar_day_date",
        "team_work_calendar_day",
        ["date"],
        unique=True,
    )

    with op.batch_alter_table("team_work_calendar_day") as batch_op:
        batch_op.alter_column("is_work_day", server_default=None)
        batch_op.alter_column("created_at", server_default=None)
        batch_op.alter_column("updated_at", server_default=None)


def downgrade():
    op.drop_index("ix_team_work_calendar_day_date", table_name="team_work_calendar_day")
    op.drop_table("team_work_calendar_day")
