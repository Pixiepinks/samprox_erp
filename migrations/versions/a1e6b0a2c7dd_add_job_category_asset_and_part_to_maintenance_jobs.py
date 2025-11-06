"""Add job category, asset and part links to maintenance jobs

Revision ID: a1e6b0a2c7dd
Revises: f73970f46c90
Create Date: 2024-08-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1e6b0a2c7dd"
down_revision = "f73970f46c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maintenance_job",
        sa.Column("job_category", sa.String(length=120), nullable=True),
    )
    op.add_column("maintenance_job", sa.Column("asset_id", sa.Integer(), nullable=True))
    op.add_column("maintenance_job", sa.Column("part_id", sa.Integer(), nullable=True))

    op.create_foreign_key(
        "fk_maintenance_job_asset",
        "maintenance_job",
        "machine_asset",
        ["asset_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_maintenance_job_part",
        "maintenance_job",
        "machine_part",
        ["part_id"],
        ["id"],
    )

    op.execute(
        sa.text(
            """
            UPDATE maintenance_job
            SET job_category = title
            WHERE job_category IS NULL OR trim(job_category) = ''
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE maintenance_job
            SET job_category = 'Mechanical / Machine Issues'
            WHERE job_category IS NULL OR trim(job_category) = ''
            """
        )
    )

    op.alter_column(
        "maintenance_job",
        "job_category",
        existing_type=sa.String(length=120),
        nullable=False,
        server_default="Mechanical / Machine Issues",
    )


def downgrade() -> None:
    op.drop_constraint("fk_maintenance_job_part", "maintenance_job", type_="foreignkey")
    op.drop_constraint("fk_maintenance_job_asset", "maintenance_job", type_="foreignkey")
    op.drop_column("maintenance_job", "part_id")
    op.drop_column("maintenance_job", "asset_id")
    op.drop_column("maintenance_job", "job_category")
