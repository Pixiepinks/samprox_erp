"""Add sales visit tracking tables

Revision ID: 0d1c3a4b5e67
Revises: f2b9a7d1c3e4
Create Date: 2025-02-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0d1c3a4b5e67"
down_revision = "f2b9a7d1c3e4"
branch_labels = None
depends_on = None


sales_visit_status = sa.Enum(
    "PENDING",
    "APPROVED",
    "REJECTED",
    "NOT_REQUIRED",
    name="sales_visit_approval_status",
)

postgres_sales_visit_status = postgresql.ENUM(
    "PENDING",
    "APPROVED",
    "REJECTED",
    "NOT_REQUIRED",
    name="sales_visit_approval_status",
    create_type=False,
)


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name if bind else ""

    if dialect == "postgresql":
        op.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_type WHERE typname = 'sales_visit_approval_status'
                    ) THEN
                        CREATE TYPE sales_visit_approval_status AS ENUM ('PENDING','APPROVED','REJECTED','NOT_REQUIRED');
                    END IF;
                END
                $$;
                """
            )
        )
        status_enum = postgres_sales_visit_status
    else:
        sales_visit_status.create(bind, checkfirst=True)
        status_enum = sales_visit_status

    op.create_table(
        "sales_visits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("visit_no", sa.String(length=40), nullable=False, unique=True, index=True),
        sa.Column("sales_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False, index=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customer.id"), nullable=True, index=True),
        sa.Column("prospect_name", sa.Text(), nullable=True),
        sa.Column("visit_date", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE"), index=True),
        sa.Column("planned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("check_in_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_in_lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("check_in_lng", sa.Numeric(10, 7), nullable=True),
        sa.Column("check_out_lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("check_out_lng", sa.Numeric(10, 7), nullable=True),
        sa.Column("distance_from_customer_m", sa.Integer(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("gps_mismatch", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("short_duration", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("manual_location_override", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("exception_reason", sa.Text(), nullable=True),
        sa.Column("approval_status", status_enum, nullable=False, server_default="NOT_REQUIRED", index=True),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id")),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("user.id")),
    )
    op.create_index("ix_sales_visits_sales_user_date", "sales_visits", ["sales_user_id", "visit_date"])
    op.create_index("ix_sales_visits_customer_id", "sales_visits", ["customer_id"])
    op.create_index("ix_sales_visits_approval_status", "sales_visits", ["approval_status"])

    op.create_table(
        "sales_visit_attachments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("visit_id", sa.String(length=36), sa.ForeignKey("sales_visits.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("file_url", sa.Text(), nullable=False),
        sa.Column("file_type", sa.String(length=40), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "sales_team_members",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("manager_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("sales_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.UniqueConstraint("manager_user_id", "sales_user_id", name="uq_sales_team_member_pair"),
    )


def downgrade():
    op.drop_table("sales_visit_attachments")
    op.drop_table("sales_team_members")
    op.drop_index("ix_sales_visits_sales_user_date", table_name="sales_visits")
    op.drop_index("ix_sales_visits_customer_id", table_name="sales_visits")
    op.drop_index("ix_sales_visits_approval_status", table_name="sales_visits")
    op.drop_table("sales_visits")

    bind = op.get_bind()
    dialect = bind.dialect.name if bind else ""
    if dialect == "postgresql":
        op.execute(sa.text("DROP TYPE IF EXISTS sales_visit_approval_status"))
    else:
        sales_visit_status.drop(bind, checkfirst=True)
