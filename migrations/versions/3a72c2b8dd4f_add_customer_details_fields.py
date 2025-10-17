"""add detailed customer fields"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3a72c2b8dd4f"
down_revision = "8e1d273b3f5f"
branch_labels = None
depends_on = None


customer_category_enum = sa.Enum("plantation", "industrial", name="customer_category")
customer_credit_term_enum = sa.Enum(
    "cash",
    "14_days",
    "30_days",
    "45_days",
    "60_days",
    name="customer_credit_term",
)
customer_transport_enum = sa.Enum("samprox_lorry", "customer_lorry", name="customer_transport_mode")
customer_type_enum = sa.Enum("regular", "seasonal", name="customer_type")


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    customer_category_enum.create(bind, checkfirst=True)
    customer_credit_term_enum.create(bind, checkfirst=True)
    customer_transport_enum.create(bind, checkfirst=True)
    customer_type_enum.create(bind, checkfirst=True)

    op.add_column(
        "customer",
        sa.Column(
            "category",
            customer_category_enum,
            nullable=False,
            server_default="plantation",
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "credit_term",
            customer_credit_term_enum,
            nullable=False,
            server_default="cash",
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "transport_mode",
            customer_transport_enum,
            nullable=False,
            server_default="samprox_lorry",
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "customer_type",
            customer_type_enum,
            nullable=False,
            server_default="regular",
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "sales_coordinator_name",
            sa.String(length=120),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "sales_coordinator_phone",
            sa.String(length=50),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "store_keeper_name",
            sa.String(length=120),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "store_keeper_phone",
            sa.String(length=50),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "payment_coordinator_name",
            sa.String(length=120),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "payment_coordinator_phone",
            sa.String(length=50),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "special_note",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )

    if dialect != "sqlite":
        op.alter_column("customer", "category", server_default=None)
        op.alter_column("customer", "credit_term", server_default=None)
        op.alter_column("customer", "transport_mode", server_default=None)
        op.alter_column("customer", "customer_type", server_default=None)
        op.alter_column("customer", "sales_coordinator_name", server_default=None)
        op.alter_column("customer", "sales_coordinator_phone", server_default=None)
        op.alter_column("customer", "store_keeper_name", server_default=None)
        op.alter_column("customer", "store_keeper_phone", server_default=None)
        op.alter_column("customer", "payment_coordinator_name", server_default=None)
        op.alter_column("customer", "payment_coordinator_phone", server_default=None)
        op.alter_column("customer", "special_note", server_default=None)


def downgrade():
    op.drop_column("customer", "special_note")
    op.drop_column("customer", "payment_coordinator_phone")
    op.drop_column("customer", "payment_coordinator_name")
    op.drop_column("customer", "store_keeper_phone")
    op.drop_column("customer", "store_keeper_name")
    op.drop_column("customer", "sales_coordinator_phone")
    op.drop_column("customer", "sales_coordinator_name")
    op.drop_column("customer", "customer_type")
    op.drop_column("customer", "transport_mode")
    op.drop_column("customer", "credit_term")
    op.drop_column("customer", "category")

    bind = op.get_bind()
    customer_type_enum.drop(bind, checkfirst=True)
    customer_transport_enum.drop(bind, checkfirst=True)
    customer_credit_term_enum.drop(bind, checkfirst=True)
    customer_category_enum.drop(bind, checkfirst=True)
