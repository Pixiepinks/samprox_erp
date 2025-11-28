"""Update vehicle number field length for petty cash weekly claims

Revision ID: e0c6f1b8f3d4
Revises: d6c3f9d0e7ab
Create Date: 2025-06-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e0c6f1b8f3d4"
down_revision = "d6c3f9d0e7ab"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"]: col for col in inspector.get_columns("petty_cash_weekly_claims")}

    if "vehicle_no" not in columns:
        op.add_column(
            "petty_cash_weekly_claims",
            sa.Column("vehicle_no", sa.String(length=20), nullable=True),
        )
    else:
        op.alter_column(
            "petty_cash_weekly_claims",
            "vehicle_no",
            existing_type=columns["vehicle_no"].get("type") or sa.String(length=100),
            type_=sa.String(length=20),
            existing_nullable=True,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"]: col for col in inspector.get_columns("petty_cash_weekly_claims")}

    if "vehicle_no" not in columns:
        return

    op.alter_column(
        "petty_cash_weekly_claims",
        "vehicle_no",
        existing_type=columns["vehicle_no"].get("type") or sa.String(length=20),
        type_=sa.String(length=100),
        existing_nullable=True,
    )
