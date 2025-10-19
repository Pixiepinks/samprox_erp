"""add team member table

Revision ID: e5b93beef9b1
Revises: b2b3c026bbf0
Create Date: 2024-07-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "e5b93beef9b1"
down_revision = "b2b3c026bbf0"
branch_labels = None
depends_on = None


t_team_member_status = sa.Enum(
    "Active",
    "On Leave",
    "Inactive",
    name="team_member_status",
)

postgres_team_member_status = postgresql.ENUM(
    "Active",
    "On Leave",
    "Inactive",
    name="team_member_status",
    create_type=False,
)


def upgrade():
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_type
                        WHERE typname = 'team_member_status'
                    ) THEN
                        CREATE TYPE team_member_status AS ENUM (
                            'Active',
                            'On Leave',
                            'Inactive'
                        );
                    END IF;
                END
                $$;
                """
            )
        )
        status_enum = postgres_team_member_status
    else:
        t_team_member_status.create(bind, checkfirst=True)
        status_enum = t_team_member_status

    op.create_table(
        "team_member",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reg_number", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("nickname", sa.String(length=120), nullable=True),
        sa.Column("epf", sa.String(length=120), nullable=True),
        sa.Column("position", sa.String(length=120), nullable=True),
        sa.Column("join_date", sa.Date(), nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reg_number"),
    )


def downgrade():
    op.drop_table("team_member")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP TYPE IF EXISTS team_member_status"))
    else:
        t_team_member_status.drop(bind, checkfirst=True)
