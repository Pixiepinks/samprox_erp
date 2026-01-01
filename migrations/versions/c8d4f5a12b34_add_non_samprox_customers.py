"""Add non Samprox customers table and link to sales visits

Revision ID: c8d4f5a12b34
Revises: 0c7a4f5e1b23
Create Date: 2025-05-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "c8d4f5a12b34"
down_revision = "0c7a4f5e1b23"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("non_samprox_customers"):
        op.create_table(
            "non_samprox_customers",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("customer_code", sa.String(length=30), nullable=False, unique=True),
            sa.Column("customer_name", sa.Text(), nullable=False),
            sa.Column("area_code", sa.String(length=5), nullable=True),
            sa.Column("city", sa.String(length=80), nullable=True),
            sa.Column("district", sa.String(length=80), nullable=True),
            sa.Column("province", sa.String(length=80), nullable=True),
            sa.Column("managed_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False, index=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False, index=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                onupdate=sa.text("now()"),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_non_samprox_customers_city_district",
            "non_samprox_customers",
            ["city", "district"],
        )

    existing_sales_visits_columns = {col.get("name") for col in inspector.get_columns("sales_visits")} if inspector.has_table("sales_visits") else set()
    if inspector.has_table("sales_visits") and "non_samprox_customer_id" not in existing_sales_visits_columns:
        with op.batch_alter_table("sales_visits") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "non_samprox_customer_id",
                    sa.String(length=36),
                    sa.ForeignKey("non_samprox_customers.id"),
                    nullable=True,
                )
            )
        op.create_index(
            "ix_sales_visits_non_samprox_customer_id",
            "sales_visits",
            ["non_samprox_customer_id"],
        )


def downgrade():
    inspector = inspect(op.get_bind())

    def _index_exists(table: str, name: str) -> bool:
        indexes = inspector.get_indexes(table) if inspector.has_table(table) else []
        return any(idx.get("name") == name for idx in indexes)

    if _index_exists("sales_visits", "ix_sales_visits_non_samprox_customer_id"):
        op.drop_index("ix_sales_visits_non_samprox_customer_id", table_name="sales_visits")
    sales_visit_columns = {col.get("name") for col in inspector.get_columns("sales_visits")} if inspector.has_table("sales_visits") else set()
    if "non_samprox_customer_id" in sales_visit_columns:
        with op.batch_alter_table("sales_visits") as batch_op:
            batch_op.drop_column("non_samprox_customer_id")

    if _index_exists("non_samprox_customers", "ix_non_samprox_customers_city_district"):
        op.drop_index("ix_non_samprox_customers_city_district", table_name="non_samprox_customers")
    if inspector.has_table("non_samprox_customers"):
        op.drop_table("non_samprox_customers")
