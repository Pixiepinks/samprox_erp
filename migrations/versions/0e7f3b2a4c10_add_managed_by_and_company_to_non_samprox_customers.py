"""Add managed_by and company columns to non_samprox_customers

Revision ID: 0e7f3b2a4c10
Revises: 5bbf48c0941d
Create Date: 2026-01-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0e7f3b2a4c10"
down_revision = "5bbf48c0941d"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("non_samprox_customers")}

    if "managed_by" not in existing_columns:
        op.add_column("non_samprox_customers", sa.Column("managed_by", sa.String(length=120), nullable=True))

    if "company" not in existing_columns:
        op.add_column("non_samprox_customers", sa.Column("company", sa.String(length=80), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("non_samprox_customers")}

    if "company" in existing_columns:
        op.drop_column("non_samprox_customers", "company")

    if "managed_by" in existing_columns:
        op.drop_column("non_samprox_customers", "managed_by")
