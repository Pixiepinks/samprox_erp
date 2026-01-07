"""Add Exsol inventory items table

Revision ID: d12ab34c56ef
Revises: 2e3f4b5c6d70
Create Date: 2026-02-10 00:00:00.000000
"""

from __future__ import annotations

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "d12ab34c56ef"
down_revision = "2e3f4b5c6d70"
branch_labels = None
depends_on = None


EXSOL_COMPANY_KEY = "exsol-engineering"
EXSOL_COMPANY_NAME = "Exsol Engineering (Pvt) Ltd"


def upgrade() -> None:
    op.create_table(
        "exsol_inventory_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("item_code", sa.String(length=50), nullable=False),
        sa.Column("item_name", sa.String(length=255), nullable=False),
        sa.Column("uom", sa.String(length=30)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "item_code", name="uq_exsol_inventory_items_company_code"),
    )
    op.create_index("ix_exsol_inventory_items_company_id", "exsol_inventory_items", ["company_id"])

    bind = op.get_bind()
    companies = sa.table(
        "companies",
        sa.column("id", sa.Integer()),
        sa.column("key", sa.String(length=64)),
        sa.column("name", sa.String(length=255)),
        sa.column("company_code_prefix", sa.String(length=4)),
        sa.column("created_at", sa.DateTime()),
    )

    existing = bind.execute(
        sa.select(companies.c.id).where(
            sa.or_(
                companies.c.name == EXSOL_COMPANY_NAME,
                companies.c.key == EXSOL_COMPANY_KEY,
            )
        )
    ).scalar()

    if not existing:
        bind.execute(
            companies.insert().values(
                key=EXSOL_COMPANY_KEY,
                name=EXSOL_COMPANY_NAME,
                company_code_prefix="E",
                created_at=datetime.utcnow(),
            )
        )


def downgrade() -> None:
    op.drop_index("ix_exsol_inventory_items_company_id", table_name="exsol_inventory_items")
    op.drop_table("exsol_inventory_items")
