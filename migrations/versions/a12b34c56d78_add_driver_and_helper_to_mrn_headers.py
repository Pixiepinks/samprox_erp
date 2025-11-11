"""add driver and helper to mrn headers"""
"""add driver and helper to mrn headers"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a12b34c56d78"
down_revision = "f9a2d1c4e5b6_merge_briquette_mix_and_finance_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("mrn_headers", sa.Column("driver_id", sa.Integer(), nullable=True))
    op.add_column("mrn_headers", sa.Column("helper1_id", sa.Integer(), nullable=True))
    op.add_column("mrn_headers", sa.Column("helper2_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_mrn_headers_driver",
        "mrn_headers",
        "team_member",
        ["driver_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_mrn_headers_helper1",
        "mrn_headers",
        "team_member",
        ["helper1_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_mrn_headers_helper2",
        "mrn_headers",
        "team_member",
        ["helper2_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("fk_mrn_headers_helper2", "mrn_headers", type_="foreignkey")
    op.drop_constraint("fk_mrn_headers_helper1", "mrn_headers", type_="foreignkey")
    op.drop_constraint("fk_mrn_headers_driver", "mrn_headers", type_="foreignkey")
    op.drop_column("mrn_headers", "helper2_id")
    op.drop_column("mrn_headers", "helper1_id")
    op.drop_column("mrn_headers", "driver_id")
