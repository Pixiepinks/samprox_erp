"""Allow responsibility assignments to team members.

Revision ID: d2b4cf5a3c11
Revises: c4ae6f0a3b21
Create Date: 2024-05-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d2b4cf5a3c11"
down_revision = "c4ae6f0a3b21"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "responsibility_task",
        sa.Column("assignee_member_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "responsibility_task",
        sa.Column("delegated_to_member_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_responsibility_task_assignee_member_id",
        "responsibility_task",
        "team_member",
        ["assignee_member_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_responsibility_task_delegated_to_member_id",
        "responsibility_task",
        "team_member",
        ["delegated_to_member_id"],
        ["id"],
    )

    op.add_column(
        "responsibility_delegation",
        sa.Column("delegate_member_id", sa.Integer(), nullable=True),
    )
    op.alter_column(
        "responsibility_delegation",
        "delegate_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_responsibility_delegation_delegate_member_id",
        "responsibility_delegation",
        "team_member",
        ["delegate_member_id"],
        ["id"],
    )
    op.drop_constraint(
        "uq_responsibility_delegation",
        "responsibility_delegation",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_responsibility_delegation",
        "responsibility_delegation",
        ["task_id", "delegate_id", "delegate_member_id"],
    )


def downgrade():
    op.drop_constraint(
        "uq_responsibility_delegation",
        "responsibility_delegation",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_responsibility_delegation",
        "responsibility_delegation",
        ["task_id", "delegate_id"],
    )
    op.drop_constraint(
        "fk_responsibility_delegation_delegate_member_id",
        "responsibility_delegation",
        type_="foreignkey",
    )
    op.alter_column(
        "responsibility_delegation",
        "delegate_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("responsibility_delegation", "delegate_member_id")

    op.drop_constraint(
        "fk_responsibility_task_delegated_to_member_id",
        "responsibility_task",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_responsibility_task_assignee_member_id",
        "responsibility_task",
        type_="foreignkey",
    )
    op.drop_column("responsibility_task", "delegated_to_member_id")
    op.drop_column("responsibility_task", "assignee_member_id")
