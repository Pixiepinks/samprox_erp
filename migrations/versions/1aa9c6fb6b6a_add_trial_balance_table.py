"""
Add trial balance table

Revision ID: 1aa9c6fb6b6a
Revises: 1e8f7b6c9d0e
Create Date: 2025-05-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "1aa9c6fb6b6a"
down_revision = "1e8f7b6c9d0e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "financial_trial_balance_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("financial_year", sa.String(length=9), nullable=False),
        sa.Column("month_index", sa.SmallInteger(), nullable=False),
        sa.Column("calendar_year", sa.Integer(), nullable=False),
        sa.Column("calendar_month", sa.Integer(), nullable=False),
        sa.Column("account_code", sa.String(length=50), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=False),
        sa.Column("ifrs_category", sa.String(length=50), nullable=False),
        sa.Column("ifrs_subcategory", sa.String(length=100), nullable=False),
        sa.Column("debit_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("credit_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "company_id",
            "financial_year",
            "month_index",
            "account_code",
            "ifrs_category",
            "ifrs_subcategory",
            name="uq_trial_balance_month_account",
        ),
    )


def downgrade():
    op.drop_table("financial_trial_balance_lines")
