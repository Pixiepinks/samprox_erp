"""add maintenance job tables

Revision ID: d3d5f3c3a4b6
Revises: 3c4296c0f5c1
Create Date: 2024-07-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d3d5f3c3a4b6"
down_revision = "3c4296c0f5c1"
branch_labels = None
depends_on = None

maintenance_status = sa.Enum("NEW", "IN_PROGRESS", "COMPLETED", name="maintenancejobstatus")


def upgrade() -> None:
    maintenance_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "maintenance_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_code", sa.String(length=40), nullable=False),
        sa.Column("job_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="Normal"),
        sa.Column("location", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("expected_completion", sa.Date(), nullable=True),
        sa.Column("status", maintenance_status, nullable=False, server_default="NEW"),
        sa.Column("prod_email", sa.String(length=255), nullable=True),
        sa.Column("maint_email", sa.String(length=255), nullable=True),
        sa.Column("prod_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("maint_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("job_started_date", sa.Date(), nullable=True),
        sa.Column("job_finished_date", sa.Date(), nullable=True),
        sa.Column("total_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("maintenance_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("assigned_to_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["assigned_to_id"], ["user.id"], name="fk_maintenance_job_assigned_to"),
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"], name="fk_maintenance_job_created_by"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_code", name="uq_maintenance_job_code"),
    )
    op.create_index("ix_maintenance_job_status", "maintenance_job", ["status"])

    op.create_table(
        "maintenance_material",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("maintenance_job_id", sa.Integer(), nullable=False),
        sa.Column("material_name", sa.String(length=255), nullable=False),
        sa.Column("units", sa.String(length=120), nullable=True),
        sa.Column("cost", sa.Numeric(12, 2), nullable=True),
        sa.ForeignKeyConstraint([
            "maintenance_job_id"
        ], ["maintenance_job.id"], name="fk_maintenance_material_job", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maintenance_material_job_id",
        "maintenance_material",
        ["maintenance_job_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_maintenance_material_job_id", table_name="maintenance_material")
    op.drop_table("maintenance_material")
    op.drop_index("ix_maintenance_job_status", table_name="maintenance_job")
    op.drop_table("maintenance_job")
    maintenance_status.drop(op.get_bind(), checkfirst=True)
