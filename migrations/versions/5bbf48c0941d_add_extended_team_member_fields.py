"""Add extended team member text fields

Revision ID: 5bbf48c0941d
Revises: f73970f46c90
Create Date: 2024-07-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5bbf48c0941d"
down_revision = "f73970f46c90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("team_member", sa.Column("personal_detail", sa.Text(), nullable=True))
    op.add_column("team_member", sa.Column("assignments", sa.Text(), nullable=True))
    op.add_column("team_member", sa.Column("training_records", sa.Text(), nullable=True))
    op.add_column("team_member", sa.Column("employment_log", sa.Text(), nullable=True))
    op.add_column("team_member", sa.Column("files", sa.Text(), nullable=True))
    op.add_column("team_member", sa.Column("assets", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("team_member", "assets")
    op.drop_column("team_member", "files")
    op.drop_column("team_member", "employment_log")
    op.drop_column("team_member", "training_records")
    op.drop_column("team_member", "assignments")
    op.drop_column("team_member", "personal_detail")

