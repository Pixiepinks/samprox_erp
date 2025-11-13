"""add progress to responsibility task

Revision ID: 0decbf7fe4f3
Revises: b94fcae1de3b
Create Date: 2024-05-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0decbf7fe4f3"
down_revision = "b94fcae1de3b"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade():
    if not _has_column("responsibility_task", "progress"):
        with op.batch_alter_table("responsibility_task") as batch_op:
            batch_op.add_column(
                sa.Column("progress", sa.Integer(), nullable=False, server_default="0")
            )

        with op.batch_alter_table("responsibility_task") as batch_op:
            batch_op.alter_column(
                "progress",
                existing_type=sa.Integer(),
                nullable=False,
                server_default=None,
            )


def downgrade():
    if _has_column("responsibility_task", "progress"):
        with op.batch_alter_table("responsibility_task") as batch_op:
            batch_op.drop_column("progress")
