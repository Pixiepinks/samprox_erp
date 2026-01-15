"""Add Exsol serial events table.

Revision ID: 8a1c4d2f6e90
Revises: 7b3a9c2d1e0f
Create Date: 2026-01-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "8a1c4d2f6e90"
down_revision = "7b3a9c2d1e0f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("exsol_serial_events"):
        op.create_table(
            "exsol_serial_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("company_key", sa.String(length=20), nullable=False, server_default="EXSOL"),
            sa.Column("serial_no", sa.String(length=60), nullable=False),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("notes", sa.String(length=255)),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    existing_indexes = {index["name"] for index in inspector.get_indexes("exsol_serial_events")}
    if "ix_exsol_serial_events_company_key" not in existing_indexes:
        op.create_index(
            "ix_exsol_serial_events_company_key",
            "exsol_serial_events",
            ["company_key"],
        )
    if "ix_exsol_serial_events_serial_no" not in existing_indexes:
        op.create_index(
            "ix_exsol_serial_events_serial_no",
            "exsol_serial_events",
            ["serial_no"],
        )
    if "ix_exsol_serial_events_company_serial" not in existing_indexes:
        op.create_index(
            "ix_exsol_serial_events_company_serial",
            "exsol_serial_events",
            ["company_key", "serial_no"],
        )


def downgrade() -> None:
    op.drop_index("ix_exsol_serial_events_company_serial", table_name="exsol_serial_events")
    op.drop_index("ix_exsol_serial_events_serial_no", table_name="exsol_serial_events")
    op.drop_index("ix_exsol_serial_events_company_key", table_name="exsol_serial_events")
    op.drop_table("exsol_serial_events")
