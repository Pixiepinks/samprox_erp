"""Add Exsol production tables

Revision ID: f1a2b3c4d5e6
Revises: fe12ab34cd56
Create Date: 2026-02-12 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "fe12ab34cd56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exsol_production_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "company_key",
            sa.String(length=20),
            nullable=False,
            server_default="EXSOL",
        ),
        sa.Column("production_date", sa.Date(), nullable=False),
        sa.Column("item_code", sa.String(length=120), nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=True),
        sa.Column("shift", sa.String(length=40), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("serial_mode", sa.String(length=20), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("user.id"),
            nullable=False,
        ),
        sa.Column("created_by_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "is_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "confirmed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("user.id"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_exsol_production_entries_quantity_positive",
        ),
    )
    op.create_index(
        "ix_exsol_production_entries_company_date",
        "exsol_production_entries",
        ["company_key", "production_date"],
    )
    op.create_index(
        "ix_exsol_production_entries_company_item",
        "exsol_production_entries",
        ["company_key", "item_code"],
    )

    op.create_table(
        "exsol_production_serials",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "entry_id",
            sa.BigInteger(),
            sa.ForeignKey("exsol_production_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_key",
            sa.String(length=20),
            nullable=False,
            server_default="EXSOL",
        ),
        sa.Column("serial_no", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "company_key",
            "serial_no",
            name="uq_exsol_production_serial_company_serial",
        ),
    )
    op.create_index(
        "ix_exsol_production_serial_company_entry",
        "exsol_production_serials",
        ["company_key", "entry_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_exsol_production_serial_company_entry",
        table_name="exsol_production_serials",
    )
    op.drop_table("exsol_production_serials")
    op.drop_index(
        "ix_exsol_production_entries_company_item",
        table_name="exsol_production_entries",
    )
    op.drop_index(
        "ix_exsol_production_entries_company_date",
        table_name="exsol_production_entries",
    )
    op.drop_table("exsol_production_entries")
