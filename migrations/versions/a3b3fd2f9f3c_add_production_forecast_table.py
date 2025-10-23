"""add production forecast table

Revision ID: a3b3fd2f9f3c
Revises: f73970f46c90
Create Date: 2024-09-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a3b3fd2f9f3c"
down_revision = "f73970f46c90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "production_forecast_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column(
            "forecast_tons",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["machine_asset.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "date",
            "asset_id",
            name="uq_production_forecast_entry_day_asset",
        ),
    )

    with op.batch_alter_table("production_forecast_entry", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_production_forecast_entry_date"),
            ["date"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_production_forecast_entry_asset_id"),
            ["asset_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("production_forecast_entry", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_production_forecast_entry_asset_id"))
        batch_op.drop_index(batch_op.f("ix_production_forecast_entry_date"))

    op.drop_table("production_forecast_entry")
