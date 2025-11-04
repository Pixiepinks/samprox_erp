"""Add loader fields to sales actual entry

Revision ID: e6da0c7d1f0f
Revises: d3d5f3c3a4b6
Create Date: 2024-11-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e6da0c7d1f0f"
down_revision = "d3d5f3c3a4b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_actual_entry",
        sa.Column("delivery_note_number", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "sales_actual_entry",
        sa.Column("weigh_slip_number", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "sales_actual_entry",
        sa.Column("loader1_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sales_actual_entry",
        sa.Column("loader2_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sales_actual_entry",
        sa.Column("loader3_id", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        "fk_sales_actual_entry_loader1",
        "sales_actual_entry",
        "team_member",
        ["loader1_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_sales_actual_entry_loader2",
        "sales_actual_entry",
        "team_member",
        ["loader2_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_sales_actual_entry_loader3",
        "sales_actual_entry",
        "team_member",
        ["loader3_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_sales_actual_entry_loader3", "sales_actual_entry", type_="foreignkey")
    op.drop_constraint("fk_sales_actual_entry_loader2", "sales_actual_entry", type_="foreignkey")
    op.drop_constraint("fk_sales_actual_entry_loader1", "sales_actual_entry", type_="foreignkey")
    op.drop_column("sales_actual_entry", "loader3_id")
    op.drop_column("sales_actual_entry", "loader2_id")
    op.drop_column("sales_actual_entry", "loader1_id")
    op.drop_column("sales_actual_entry", "weigh_slip_number")
    op.drop_column("sales_actual_entry", "delivery_note_number")
