"""expand perf_metric_value precision

Revision ID: c4ae6f0a3b21
Revises: a97a7fa6d280
Create Date: 2025-11-15 05:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4ae6f0a3b21"
down_revision = "a97a7fa6d280"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("responsibility_task") as batch_op:
        batch_op.alter_column(
            "perf_metric_value",
            type_=sa.Numeric(18, 4),
            existing_type=sa.Numeric(6, 1),
            existing_nullable=True,
        )


def downgrade():
    with op.batch_alter_table("responsibility_task") as batch_op:
        batch_op.alter_column(
            "perf_metric_value",
            type_=sa.Numeric(6, 1),
            existing_type=sa.Numeric(18, 4),
            existing_nullable=True,
        )
