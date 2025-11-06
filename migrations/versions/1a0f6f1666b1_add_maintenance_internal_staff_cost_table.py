"""Add maintenance internal staff cost table

Revision ID: 1a0f6f1666b1
Revises: 0c5b9f256d4e
Create Date: 2025-02-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1a0f6f1666b1"
down_revision = "0c5b9f256d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_internal_staff_cost",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("maintenance_job_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("work_description", sa.String(length=255), nullable=False),
        sa.Column("engaged_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("hourly_rate", sa.Numeric(10, 2), nullable=True),
        sa.Column("cost", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["maintenance_job_id"],
            ["maintenance_job.id"],
            name="fk_internal_staff_cost_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["team_member.id"],
            name="fk_internal_staff_cost_employee",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_internal_staff_cost_job_id",
        "maintenance_internal_staff_cost",
        ["maintenance_job_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_internal_staff_cost_job_id",
        table_name="maintenance_internal_staff_cost",
    )
    op.drop_table("maintenance_internal_staff_cost")
