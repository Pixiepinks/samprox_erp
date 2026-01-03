"""Add audit fields for dealer bulk import

Revision ID: 1c2a4f7b8d90
Revises: fb3e3d7c9a1b
Create Date: 2026-03-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import reflection

# revision identifiers, used by Alembic.
revision = "1c2a4f7b8d90"
down_revision = "fb3e3d7c9a1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = reflection.Inspector.from_engine(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("non_samprox_customers")}

    with op.batch_alter_table("non_samprox_customers") as batch_op:
        if "created_by" not in existing_columns:
            batch_op.add_column(sa.Column("created_by", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_non_samprox_customers_created_by_user",
                "user",
                ["created_by"],
                ["id"],
            )
        if "source" not in existing_columns:
            batch_op.add_column(sa.Column("source", sa.String(length=50), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = reflection.Inspector.from_engine(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("non_samprox_customers")}

    with op.batch_alter_table("non_samprox_customers") as batch_op:
        if "source" in existing_columns:
            batch_op.drop_column("source")
        if "created_by" in existing_columns:
            batch_op.drop_constraint("fk_non_samprox_customers_created_by_user", type_="foreignkey")
            batch_op.drop_column("created_by")
