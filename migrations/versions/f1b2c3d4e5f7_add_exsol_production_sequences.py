"""Add sequences for Exsol production IDs

Revision ID: f1b2c3d4e5f7
Revises: f1a2b3c4d5e6
Create Date: 2026-02-12 00:10:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "f1b2c3d4e5f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _ensure_sequence(table: str) -> None:
    sequence = f"{table}_id_seq"
    op.execute(
        f"CREATE SEQUENCE IF NOT EXISTS {sequence} OWNED BY {table}.id"
    )
    op.execute(
        "SELECT setval("
        f"'{sequence}', COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
        ")"
    )
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT nextval('{sequence}')"
    )


def _drop_sequence(table: str) -> None:
    sequence = f"{table}_id_seq"
    op.execute(f"ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT")
    op.execute(f"DROP SEQUENCE IF EXISTS {sequence}")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    _ensure_sequence("exsol_production_entries")
    _ensure_sequence("exsol_production_serials")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    _drop_sequence("exsol_production_serials")
    _drop_sequence("exsol_production_entries")
