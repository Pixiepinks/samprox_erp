"""Add responsibility delegations table"""

"""Add responsibility delegations table"""

from alembic import op
import sqlalchemy as sa


revision = "1f0f3d2c7e45"
down_revision = "0decbf7fe4f3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "responsibility_delegation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("responsibility_task.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delegate_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("allocated_value", sa.Numeric(18, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "delegate_id", name="uq_responsibility_delegation"),
    )
    op.create_index(
        "ix_responsibility_delegation_task_id",
        "responsibility_delegation",
        ["task_id"],
    )


def downgrade():
    op.drop_index(
        "ix_responsibility_delegation_task_id",
        table_name="responsibility_delegation",
    )
    op.drop_table("responsibility_delegation")
