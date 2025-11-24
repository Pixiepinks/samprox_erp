"""Add maintenance job part mapping table

Revision ID: c0b5a0f6b7d2
Revises: ba5b85c5c1df
Create Date: 2026-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c0b5a0f6b7d2"
down_revision = "ba5b85c5c1df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_job_part",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("part_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["maintenance_job.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["part_id"], ["machine_part.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "part_id"),
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO maintenance_job_part (job_id, part_id) "
            "SELECT id, part_id FROM maintenance_job WHERE part_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_table("maintenance_job_part")
