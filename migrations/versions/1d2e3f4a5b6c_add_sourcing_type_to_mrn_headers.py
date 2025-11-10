"""
Add sourcing type to MRN headers.

Revision ID: 1d2e3f4a5b6c
Revises: f9a2d1c4e5b6
Create Date: 2024-10-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1d2e3f4a5b6c"
down_revision = "f9a2d1c4e5b6"
branch_labels = None
depends_on = None


INTERNAL_VEHICLES = ("LI-1795", "LB-3237")


def upgrade():
    with op.batch_alter_table("mrn_headers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "sourcing_type",
                sa.String(length=40),
                nullable=False,
                server_default="Ownsourcing",
            )
        )

    internal_list = ", ".join(f"'{value}'" for value in INTERNAL_VEHICLES)
    op.execute(
        sa.text(
            f"""
            UPDATE mrn_headers
            SET sourcing_type = 'Outside'
            WHERE sourcing_type = 'Ownsourcing'
              AND supplier_name_free IS NOT NULL
              AND supplier_name_free NOT IN ({internal_list})
            """
        )
    )

    with op.batch_alter_table("mrn_headers", schema=None) as batch_op:
        batch_op.alter_column("sourcing_type", server_default=None)


def downgrade():
    with op.batch_alter_table("mrn_headers", schema=None) as batch_op:
        batch_op.drop_column("sourcing_type")
