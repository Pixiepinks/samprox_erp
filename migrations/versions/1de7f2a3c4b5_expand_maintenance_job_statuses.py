"""Expand maintenance job statuses and store as strings"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "1de7f2a3c4b5"
down_revision = "fb3e3d7c9a1b"
branch_labels = None
depends_on = None


old_status_type = sa.Enum("NEW", "IN_PROGRESS", "COMPLETED", name="maintenancejobstatus")
new_status_length = sa.String(length=50)


def upgrade():
    with op.batch_alter_table("maintenance_job") as batch_op:
        batch_op.alter_column("status", server_default=None)
        batch_op.alter_column(
            "status",
            existing_type=old_status_type,
            type_=new_status_length,
            nullable=False,
        )

    op.execute(sa.text("DROP TYPE IF EXISTS maintenancejobstatus"))

    op.execute(
        sa.text("UPDATE maintenance_job SET status = 'COMPLETED_VERIFIED' WHERE status = 'COMPLETED'")
    )
    op.execute(sa.text("UPDATE maintenance_job SET status = 'SUBMITTED' WHERE status = 'NEW'"))

    with op.batch_alter_table("maintenance_job") as batch_op:
        batch_op.alter_column("status", server_default="SUBMITTED")


def downgrade():
    downgrade_enum = sa.Enum("NEW", "IN_PROGRESS", "COMPLETED", name="maintenancejobstatus")
    op.execute(sa.text("UPDATE maintenance_job SET status = 'COMPLETED' WHERE status = 'COMPLETED_VERIFIED'"))
    op.execute(sa.text("UPDATE maintenance_job SET status = 'IN_PROGRESS' WHERE status = 'IN_PROGRESS'"))
    op.execute(sa.text("UPDATE maintenance_job SET status = 'NEW' WHERE status = 'SUBMITTED'"))
    with op.batch_alter_table("maintenance_job") as batch_op:
        batch_op.alter_column("status", server_default=None)
        batch_op.alter_column(
            "status",
            existing_type=new_status_length,
            type_=downgrade_enum,
            nullable=False,
            server_default="NEW",
        )
