"""Add maintenance outsourced service table

Revision ID: 0c5b9f256d4e
Revises: f8c9d9d92c14
Create Date: 2025-02-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0c5b9f256d4e"
down_revision = "f8c9d9d92c14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_outsourced_service",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("maintenance_job_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("service_description", sa.String(length=255), nullable=False),
        sa.Column("engaged_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("cost", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["maintenance_job_id"],
            ["maintenance_job.id"],
            name="fk_maintenance_outsourced_service_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["service_supplier.id"],
            name="fk_maintenance_outsourced_service_supplier",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maintenance_outsourced_service_job_id",
        "maintenance_outsourced_service",
        ["maintenance_job_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_maintenance_outsourced_service_job_id",
        table_name="maintenance_outsourced_service",
    )
    op.drop_table("maintenance_outsourced_service")
