"""remove team member status column"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "1a4c8fbcf252"
down_revision = "f73970f46c90"
branch_labels = None
depends_on = None


_status_enum = sa.Enum(
    "Active",
    "On Leave",
    "Inactive",
    name="team_member_status",
)
_postgres_status_enum = postgresql.ENUM(
    "Active",
    "On Leave",
    "Inactive",
    name="team_member_status",
    create_type=False,
)


def _table_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade():
    bind = op.get_bind()
    columns = _table_columns("team_member")

    if "status" in columns:
        with op.batch_alter_table("team_member") as batch_op:
            batch_op.drop_column("status")

    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP TYPE IF EXISTS team_member_status"))
    else:
        if bind.dialect.name != "sqlite":
            _status_enum.drop(bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_type WHERE typname = 'team_member_status'
                    ) THEN
                        CREATE TYPE team_member_status AS ENUM ('Active', 'On Leave', 'Inactive');
                    END IF;
                END
                $$;
                """
            )
        )
        enum_type = _postgres_status_enum
    else:
        if dialect != "sqlite":
            _status_enum.create(bind, checkfirst=True)
        enum_type = _status_enum

    with op.batch_alter_table("team_member") as batch_op:
        batch_op.add_column(
            sa.Column("status", enum_type, nullable=False, server_default="Active"),
        )

    op.execute(sa.text("UPDATE team_member SET status = 'Active' WHERE status IS NULL"))

    with op.batch_alter_table("team_member") as batch_op:
        batch_op.alter_column("status", server_default=None)
