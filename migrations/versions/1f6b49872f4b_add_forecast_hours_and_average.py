"""Add forecast hours and average hourly production columns

Revision ID: 1f6b49872f4b
Revises: cdd3c7fafc8d
Create Date: 2024-10-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1f6b49872f4b"
down_revision = "cdd3c7fafc8d"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("production_forecast_entry", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("forecast_hours", sa.Float(), nullable=False, server_default="0"),
        )
        batch_op.add_column(
            sa.Column(
                "average_hourly_production",
                sa.Float(),
                nullable=False,
                server_default="0",
            ),
        )

    with op.batch_alter_table("production_forecast_entry", schema=None) as batch_op:
        batch_op.alter_column("forecast_hours", server_default=None)
        batch_op.alter_column("average_hourly_production", server_default=None)


def downgrade():
    with op.batch_alter_table("production_forecast_entry", schema=None) as batch_op:
        batch_op.drop_column("average_hourly_production")
        batch_op.drop_column("forecast_hours")
