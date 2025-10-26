"""add weight fields to MRN header"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f5d07c0a4b9b"
down_revision = "9b62f2a53f2c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "mrn_headers",
        sa.Column("weigh_in_weight_kg", sa.Numeric(12, 3), nullable=True),
    )
    op.add_column(
        "mrn_headers",
        sa.Column("weigh_out_weight_kg", sa.Numeric(12, 3), nullable=True),
    )
    op.create_check_constraint(
        "ck_mrn_out_weight_greater_than_in",
        "mrn_headers",
        "weigh_out_weight_kg IS NULL OR weigh_in_weight_kg IS NULL OR weigh_out_weight_kg > weigh_in_weight_kg",
    )


def downgrade():
    op.drop_constraint("ck_mrn_out_weight_greater_than_in", "mrn_headers", type_="check")
    op.drop_column("mrn_headers", "weigh_out_weight_kg")
    op.drop_column("mrn_headers", "weigh_in_weight_kg")

