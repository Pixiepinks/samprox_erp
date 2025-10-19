"""Ensure team member optional columns exist

Revision ID: f73970f46c90
Revises: e5b93beef9b1
Create Date: 2024-07-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func, text


# revision identifiers, used by Alembic.
revision = "f73970f46c90"
down_revision = "e5b93beef9b1"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade():
    columns = _table_columns("team_member")

    if "image_url" not in columns:
        op.add_column(
            "team_member",
            sa.Column("image_url", sa.String(length=500), nullable=True),
        )

    if "created_at" not in columns:
        op.add_column(
            "team_member",
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=func.now(),
                nullable=False,
            ),
        )
        op.execute(text("UPDATE team_member SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
        with op.batch_alter_table("team_member") as batch_op:
            batch_op.alter_column("created_at", server_default=None)

    columns = _table_columns("team_member")
    if "updated_at" not in columns:
        op.add_column(
            "team_member",
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=func.now(),
                nullable=False,
            ),
        )
        op.execute(text("UPDATE team_member SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"))
        with op.batch_alter_table("team_member") as batch_op:
            batch_op.alter_column("updated_at", server_default=None)


def downgrade():
    columns = _table_columns("team_member")

    if "updated_at" in columns:
        op.drop_column("team_member", "updated_at")

    columns = _table_columns("team_member")
    if "created_at" in columns:
        op.drop_column("team_member", "created_at")

    columns = _table_columns("team_member")
    if "image_url" in columns:
        op.drop_column("team_member", "image_url")
