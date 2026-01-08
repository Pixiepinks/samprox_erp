"""Ensure Exsol production indexes and constraints.

Revision ID: b1c2d3e4f5f8
Revises: f1b2c3d4e5f7
Create Date: 2026-02-12 01:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5f8"
down_revision = "f1b2c3d4e5f7"
branch_labels = None
depends_on = None


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _unique_exists(bind, table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(
        constraint["name"] == constraint_name for constraint in inspector.get_unique_constraints(table_name)
    )


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_exsol_production_entries_company_date "
            "ON exsol_production_entries (company_key, production_date)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_exsol_production_entries_company_item "
            "ON exsol_production_entries (company_key, item_code)"
        )
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_exsol_production_serial_company_serial "
            "ON exsol_production_serials (company_key, serial_no)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_exsol_production_serial_company_entry "
            "ON exsol_production_serials (company_key, entry_id)"
        )
        return

    if not _index_exists(bind, "exsol_production_entries", "ix_exsol_production_entries_company_date"):
        op.create_index(
            "ix_exsol_production_entries_company_date",
            "exsol_production_entries",
            ["company_key", "production_date"],
        )
    if not _index_exists(bind, "exsol_production_entries", "ix_exsol_production_entries_company_item"):
        op.create_index(
            "ix_exsol_production_entries_company_item",
            "exsol_production_entries",
            ["company_key", "item_code"],
        )
    if not _unique_exists(bind, "exsol_production_serials", "uq_exsol_production_serial_company_serial"):
        op.create_unique_constraint(
            "uq_exsol_production_serial_company_serial",
            "exsol_production_serials",
            ["company_key", "serial_no"],
        )
    if not _index_exists(bind, "exsol_production_serials", "ix_exsol_production_serial_company_entry"):
        op.create_index(
            "ix_exsol_production_serial_company_entry",
            "exsol_production_serials",
            ["company_key", "entry_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_exsol_production_serial_company_entry")
        op.execute("DROP INDEX IF EXISTS uq_exsol_production_serial_company_serial")
        op.execute("DROP INDEX IF EXISTS ix_exsol_production_entries_company_item")
        op.execute("DROP INDEX IF EXISTS ix_exsol_production_entries_company_date")
        return

    op.drop_index(
        "ix_exsol_production_serial_company_entry",
        table_name="exsol_production_serials",
    )
    op.drop_constraint(
        "uq_exsol_production_serial_company_serial",
        "exsol_production_serials",
        type_="unique",
    )
    op.drop_index(
        "ix_exsol_production_entries_company_item",
        table_name="exsol_production_entries",
    )
    op.drop_index(
        "ix_exsol_production_entries_company_date",
        table_name="exsol_production_entries",
    )
