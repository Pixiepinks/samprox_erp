"""Add accuracy fields to sales visits

Revision ID: 0c7a4f5e1b23
Revises: 0d1c3a4b5e67
Create Date: 2025-02-16 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0c7a4f5e1b23"
down_revision = "0d1c3a4b5e67"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("sales_visits") as batch_op:
        batch_op.add_column(sa.Column("check_in_accuracy_m", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("check_out_accuracy_m", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("sales_visits") as batch_op:
        batch_op.drop_column("check_out_accuracy_m")
        batch_op.drop_column("check_in_accuracy_m")
