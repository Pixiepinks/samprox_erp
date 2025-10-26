"""Add material master tables and MRN headers

Revision ID: 1b6e6de8c4a0
Revises: f73970f46c90
Create Date: 2024-08-20 00:00:00.000000
"""

import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1b6e6de8c4a0"
down_revision = "f73970f46c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("tax_id", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "material_categories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "material_types",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("category_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["category_id"], ["material_categories.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_id", "name", name="uq_material_type_category_name"),
    )

    op.create_table(
        "mrn_headers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mrn_no", sa.String(length=60), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=True),
        sa.Column("supplier_name_free", sa.String(length=255), nullable=True),
        sa.Column("category_id", sa.String(length=36), nullable=False),
        sa.Column("material_type_id", sa.String(length=36), nullable=False),
        sa.Column("qty_ton", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("wet_factor", sa.Numeric(6, 3), nullable=False, server_default="1.000"),
        sa.Column("approved_unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("weighing_slip_no", sa.String(length=80), nullable=False),
        sa.Column("weigh_in_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("weigh_out_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("security_officer_name", sa.String(length=120), nullable=False),
        sa.Column("authorized_person_name", sa.String(length=120), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["material_categories.id"], ),
        sa.ForeignKeyConstraint(["material_type_id"], ["material_types.id"], ),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mrn_no"),
        sa.CheckConstraint("qty_ton > 0", name="ck_mrn_qty_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_mrn_unit_price_non_negative"),
        sa.CheckConstraint("wet_factor >= 0", name="ck_mrn_wet_factor_non_negative"),
        sa.CheckConstraint("approved_unit_price >= 0", name="ck_mrn_approved_unit_price_non_negative"),
        sa.CheckConstraint("amount >= 0", name="ck_mrn_amount_non_negative"),
        sa.CheckConstraint("weigh_out_time >= weigh_in_time", name="ck_mrn_weigh_out_after_in"),
    )

    category_ids = {name: str(uuid.uuid4()) for name in (
        "Product Material",
        "Packing Material",
        "Repair Material",
        "Maintenance Material",
    )}
    categories_table = sa.table(
        "material_categories",
        sa.column("id", sa.String(length=36)),
        sa.column("name", sa.String(length=80)),
    )
    op.bulk_insert(
        categories_table,
        [{"id": ident, "name": name} for name, ident in category_ids.items()],
    )

    types_table = sa.table(
        "material_types",
        sa.column("id", sa.String(length=36)),
        sa.column("category_id", sa.String(length=36)),
        sa.column("name", sa.String(length=120)),
        sa.column("is_active", sa.Boolean()),
    )
    product_id = category_ids["Product Material"]
    op.bulk_insert(
        types_table,
        [
            {"id": str(uuid.uuid4()), "category_id": product_id, "name": name, "is_active": True}
            for name in ("wood shaving", "saw dust", "wood powder", "peanut husk")
        ],
    )


def downgrade() -> None:
    op.drop_table("mrn_headers")
    op.drop_table("material_types")
    op.drop_table("material_categories")
    op.drop_table("suppliers")
