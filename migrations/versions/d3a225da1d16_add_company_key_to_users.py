"""add company key to users

Revision ID: d3a225da1d16
Revises: d2b4cf5a3c11
Create Date: 2025-11-17 08:01:56.618937

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3a225da1d16'
down_revision = 'd2b4cf5a3c11'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("company_key", sa.String(length=64), nullable=True))
    op.create_index("ix_user_company_key", "user", ["company_key"], unique=False)


def downgrade():
    op.drop_index("ix_user_company_key", table_name="user")
    op.drop_column("user", "company_key")
