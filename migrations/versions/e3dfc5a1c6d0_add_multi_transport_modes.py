"""Add multi transport modes to customers and sales entries

Revision ID: e3dfc5a1c6d0
Revises: 0decbf7fe4f3
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e3dfc5a1c6d0"
down_revision = "0decbf7fe4f3"
branch_labels = None
depends_on = None


customer_transport_enum = sa.Enum(
    "samprox_lorry", "customer_lorry", name="customer_transport_mode"
)


def upgrade() -> None:
    op.add_column("customer", sa.Column("allowed_transport_modes", sa.Text(), nullable=True))
    op.add_column("customer", sa.Column("default_transport_mode", sa.Text(), nullable=True))

    bind = op.get_bind()
    customer_table = sa.table(
        "customer",
        sa.column("transport_mode", customer_transport_enum),
        sa.column("allowed_transport_modes", sa.Text()),
        sa.column("default_transport_mode", sa.Text()),
    )

    bind.execute(
        customer_table.update()
        .where(customer_table.c.transport_mode == "samprox_lorry")
        .values(
            allowed_transport_modes="samprox_lorry",
            default_transport_mode="samprox_lorry",
        )
    )
    bind.execute(
        customer_table.update()
        .where(customer_table.c.transport_mode == "customer_lorry")
        .values(
            allowed_transport_modes="customer_lorry",
            default_transport_mode="customer_lorry",
        )
    )

    op.add_column("sales_actual_entry", sa.Column("transport_mode_used", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_actual_entry", "transport_mode_used")
    op.drop_column("customer", "default_transport_mode")
    op.drop_column("customer", "allowed_transport_modes")
