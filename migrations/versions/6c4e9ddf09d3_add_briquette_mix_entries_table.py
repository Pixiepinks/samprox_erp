"""Add briquette mix entries table

Revision ID: 6c4e9ddf09d3
Revises: 0c5b9f256d4e
Create Date: 2025-02-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6c4e9ddf09d3"
down_revision = "0c5b9f256d4e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "briquette_mix_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("dry_factor", sa.Numeric(10, 4), nullable=True),
        sa.Column("sawdust_qty_ton", sa.Numeric(12, 3), nullable=False, server_default="0.000"),
        sa.Column("wood_shaving_qty_ton", sa.Numeric(12, 3), nullable=False, server_default="0.000"),
        sa.Column("wood_powder_qty_ton", sa.Numeric(12, 3), nullable=False, server_default="0.000"),
        sa.Column("peanut_husk_qty_ton", sa.Numeric(12, 3), nullable=False, server_default="0.000"),
        sa.Column("fire_cut_qty_ton", sa.Numeric(12, 3), nullable=False, server_default="0.000"),
        sa.Column("total_material_cost", sa.Numeric(14, 2), nullable=False, server_default="0.00"),
        sa.Column("unit_cost_per_kg", sa.Numeric(12, 4), nullable=False, server_default="0.0000"),
        sa.Column("total_output_kg", sa.Numeric(14, 3), nullable=False, server_default="0.000"),
        sa.Column(
            "cost_breakdown",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('now()'),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", name="uq_briquette_mix_entries_date"),
    )
    op.create_index(
        op.f("ix_briquette_mix_entries_date"),
        "briquette_mix_entries",
        ["date"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_briquette_mix_entries_date"), table_name="briquette_mix_entries")
    op.drop_table("briquette_mix_entries")
