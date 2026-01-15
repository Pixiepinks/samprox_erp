"""Add Exsol serial events table.

Revision ID: 37af3761a65b
Revises: fe12ab34cd56
Create Date: 2026-02-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "37af3761a65b"
down_revision = "fe12ab34cd56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exsol_serial_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("company_key", sa.String(length=20), nullable=False, server_default="EXSOL"),
        sa.Column("item_code", sa.String(length=60), nullable=False),
        sa.Column("serial_number", sa.String(length=60), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("event_date", sa.DateTime(), nullable=False),
        sa.Column("ref_type", sa.String(length=60)),
        sa.Column("ref_id", sa.String(length=64)),
        sa.Column("ref_no", sa.String(length=120)),
        sa.Column("customer_id", sa.String(length=36)),
        sa.Column("customer_name", sa.String(length=255)),
        sa.Column("notes", sa.String(length=500)),
        sa.Column("meta_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["customer_id"], ["non_samprox_customers.id"]),
    )
    op.create_index(
        "ix_exsol_serial_events_company_item_serial",
        "exsol_serial_events",
        ["company_key", "item_code", "serial_number"],
    )
    op.create_index(
        "ix_exsol_serial_events_event_date",
        "exsol_serial_events",
        ["event_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_exsol_serial_events_event_date", table_name="exsol_serial_events")
    op.drop_index("ix_exsol_serial_events_company_item_serial", table_name="exsol_serial_events")
    op.drop_table("exsol_serial_events")
