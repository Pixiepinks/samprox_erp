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
    existing_columns = {col["name"] for col in inspector.get_columns("exsol_serial_events")}
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
        existing_columns = {"id", "company_key", "serial_no", "event_type", "notes", "created_at"}
    else:
        if "company_key" not in existing_columns:
            op.add_column(
                "exsol_serial_events",
                sa.Column("company_key", sa.String(length=20), nullable=False, server_default="EXSOL"),
            )
        if "serial_no" not in existing_columns:
            op.add_column(
                "exsol_serial_events",
                sa.Column("serial_no", sa.String(length=60), nullable=True),
            )
            op.execute(
                "UPDATE exsol_serial_events SET serial_no = 'UNKNOWN' WHERE serial_no IS NULL"
            )
            op.execute(
                "ALTER TABLE exsol_serial_events ALTER COLUMN serial_no SET NOT NULL"
            )
        if "event_type" not in existing_columns:
            op.add_column(
                "exsol_serial_events",
                sa.Column("event_type", sa.String(length=40), nullable=False),
            )
        if "notes" not in existing_columns:
            op.add_column(
                "exsol_serial_events",
                sa.Column("notes", sa.String(length=255)),
            )
        if "created_at" not in existing_columns:
            op.add_column(
                "exsol_serial_events",
                sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            )
        existing_columns = {col["name"] for col in inspector.get_columns("exsol_serial_events")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("exsol_serial_events")}
    if "company_key" in existing_columns and "ix_exsol_serial_events_company_key" not in existing_indexes:
        op.create_index(
            "ix_exsol_serial_events_company_key",
            "exsol_serial_events",
            ["company_key"],
        )
    if "serial_no" in existing_columns and "ix_exsol_serial_events_serial_no" not in existing_indexes:
        op.create_index(
            "ix_exsol_serial_events_serial_no",
            "exsol_serial_events",
            ["serial_no"],
        )
    if (
        "company_key" in existing_columns
        and "serial_no" in existing_columns
        and "ix_exsol_serial_events_company_serial" not in existing_indexes
    ):
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
