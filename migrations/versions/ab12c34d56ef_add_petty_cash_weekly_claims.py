"""add petty cash weekly claims tables

Revision ID: ab12c34d56ef
Revises: fb3e3d7c9a1b
Create Date: 2024-07-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ab12c34d56ef"
down_revision = "fb3e3d7c9a1b"
branch_labels = None
depends_on = None


STATUS_ENUM_NAME = "pettycashstatus"


def upgrade():
    petty_cash_status = sa.Enum(
        "Draft",
        "Submitted",
        "Approved",
        "Rejected",
        "Paid",
        name=STATUS_ENUM_NAME,
    )
    bind = op.get_bind()
    petty_cash_status.create(bind, checkfirst=True)

    op.create_table(
        "petty_cash_weekly_claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("employee_name", sa.String(length=255), nullable=False),
        sa.Column("company_id", sa.String(length=64), nullable=True),
        sa.Column("sheet_no", sa.String(length=64), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("week_end_date", sa.Date(), nullable=False),
        sa.Column("vehicle_no", sa.String(length=100), nullable=True),
        sa.Column("area_visited", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "Draft",
                "Submitted",
                "Approved",
                "Rejected",
                "Paid",
                name=STATUS_ENUM_NAME,
                create_type=False,
            ),
            nullable=False,
            server_default="Draft",
        ),
        sa.Column(
            "total_expenses",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"],),
        sa.ForeignKeyConstraint(["employee_id"], ["user.id"],),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sheet_no"),
    )
    op.create_index(
        op.f("ix_petty_cash_weekly_claims_company_id"),
        "petty_cash_weekly_claims",
        ["company_id"],
    )
    op.create_index(
        op.f("ix_petty_cash_weekly_claims_week_start_date"),
        "petty_cash_weekly_claims",
        ["week_start_date"],
    )

    op.create_table(
        "petty_cash_weekly_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("line_order", sa.Integer(), nullable=False),
        sa.Column("expense_type", sa.String(length=255), nullable=True),
        sa.Column(
            "mon_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "tue_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "wed_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "thu_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "fri_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "sat_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "sun_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "row_total",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["petty_cash_weekly_claims.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_petty_cash_weekly_lines_claim_id"),
        "petty_cash_weekly_lines",
        ["claim_id"],
    )
    op.create_index(
        op.f("ix_petty_cash_weekly_lines_line_order"),
        "petty_cash_weekly_lines",
        ["line_order"],
    )


def downgrade():
    op.drop_index(op.f("ix_petty_cash_weekly_lines_line_order"), table_name="petty_cash_weekly_lines")
    op.drop_index(op.f("ix_petty_cash_weekly_lines_claim_id"), table_name="petty_cash_weekly_lines")
    op.drop_table("petty_cash_weekly_lines")

    op.drop_index(op.f("ix_petty_cash_weekly_claims_week_start_date"), table_name="petty_cash_weekly_claims")
    op.drop_index(op.f("ix_petty_cash_weekly_claims_company_id"), table_name="petty_cash_weekly_claims")
    op.drop_table("petty_cash_weekly_claims")

    bind = op.get_bind()
    petty_cash_status = sa.Enum(
        "Draft",
        "Submitted",
        "Approved",
        "Rejected",
        "Paid",
        name=STATUS_ENUM_NAME,
    )
    petty_cash_status.drop(bind, checkfirst=True)
