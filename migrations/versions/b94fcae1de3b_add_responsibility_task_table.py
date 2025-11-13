"""add responsibility task table"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b94fcae1de3b"
down_revision = "a7b4d6e8c9f1"
branch_labels = None
depends_on = None


RESPONSIBILITY_RECURRENCE_VALUES = (
    "does_not_repeat",
    "monday_to_friday",
    "daily",
    "weekly",
    "monthly",
    "annually",
    "custom",
)

RESPONSIBILITY_STATUS_VALUES = (
    "planned",
    "in_progress",
    "completed",
)

RESPONSIBILITY_ACTION_VALUES = (
    "done",
    "delegated",
    "deferred",
    "discussed",
    "deleted",
)

responsibility_recurrence_enum = sa.Enum(
    *RESPONSIBILITY_RECURRENCE_VALUES, name="responsibilityrecurrence"
)

responsibility_status_enum = sa.Enum(
    *RESPONSIBILITY_STATUS_VALUES, name="responsibilitytaskstatus"
)

responsibility_action_enum = sa.Enum(
    *RESPONSIBILITY_ACTION_VALUES, name="responsibilityaction"
)

postgres_responsibility_recurrence = postgresql.ENUM(
    *RESPONSIBILITY_RECURRENCE_VALUES,
    name="responsibilityrecurrence",
    create_type=False,
)

postgres_responsibility_status = postgresql.ENUM(
    *RESPONSIBILITY_STATUS_VALUES,
    name="responsibilitytaskstatus",
    create_type=False,
)

postgres_responsibility_action = postgresql.ENUM(
    *RESPONSIBILITY_ACTION_VALUES,
    name="responsibilityaction",
    create_type=False,
)


def _ensure_enum(bind, enum, postgres_enum):
    if bind.dialect.name == "postgresql":
        name = postgres_enum.name
        values = ", ".join(f"'{value}'" for value in postgres_enum.enums)
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_type WHERE typname = '{name}'
                    ) THEN
                        CREATE TYPE {name} AS ENUM ({values});
                    END IF;
                END
                $$;
                """
            )
        )
        return postgres_enum

    enum.create(bind, checkfirst=True)
    return enum


def upgrade():
    bind = op.get_bind()

    recurrence_enum = _ensure_enum(
        bind, responsibility_recurrence_enum, postgres_responsibility_recurrence
    )
    status_enum = _ensure_enum(
        bind, responsibility_status_enum, postgres_responsibility_status
    )
    action_enum = _ensure_enum(
        bind, responsibility_action_enum, postgres_responsibility_action
    )

    op.create_table(
        "responsibility_task",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("number", sa.String(length=20), nullable=False, unique=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("scheduled_for", sa.Date(), nullable=False),
        sa.Column("recurrence", recurrence_enum, nullable=False),
        sa.Column("custom_weekdays", sa.String(length=120), nullable=True),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("action", action_enum, nullable=False),
        sa.Column("action_notes", sa.Text(), nullable=True),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("assigner_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("assignee_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("delegated_to_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_index(
        "ix_responsibility_task_scheduled_for",
        "responsibility_task",
        ["scheduled_for", "created_at"],
    )
    op.create_index(
        "ix_responsibility_task_assignments",
        "responsibility_task",
        ["assignee_id", "delegated_to_id"],
    )


def downgrade():
    op.drop_index(
        "ix_responsibility_task_assignments", table_name="responsibility_task"
    )
    op.drop_index(
        "ix_responsibility_task_scheduled_for", table_name="responsibility_task"
    )
    op.drop_table("responsibility_task")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name in (
            postgres_responsibility_action.name,
            postgres_responsibility_status.name,
            postgres_responsibility_recurrence.name,
        ):
            op.execute(sa.text(f"DROP TYPE IF EXISTS {name}"))
    else:
        responsibility_action_enum.drop(bind, checkfirst=True)
        responsibility_status_enum.drop(bind, checkfirst=True)
        responsibility_recurrence_enum.drop(bind, checkfirst=True)
