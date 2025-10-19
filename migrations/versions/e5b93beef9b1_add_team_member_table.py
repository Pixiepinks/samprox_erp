"""add team member table

Revision ID: e5b93beef9b1
Revises: b2b3c026bbf0
Create Date: 2024-07-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


t_team_member_status = sa.Enum(
    "Active",
    "On Leave",
    "Inactive",
    name="team_member_status",
)


def upgrade():
    bind = op.get_bind()
    t_team_member_status.create(bind, checkfirst=True)

    op.create_table(
        "team_member",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reg_number", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("nickname", sa.String(length=120), nullable=True),
        sa.Column("epf", sa.String(length=120), nullable=True),
        sa.Column("position", sa.String(length=120), nullable=True),
        sa.Column("join_date", sa.Date(), nullable=False),
        sa.Column("status", t_team_member_status, nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reg_number"),
    )


def downgrade():
    op.drop_table("team_member")
    t_team_member_status.drop(op.get_bind(), checkfirst=True)
