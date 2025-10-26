"""replace material categories/types with item master"""

import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "cc1dd0e5b4ce"
down_revision = "7d4694ff3d6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_items",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name"),
    )

    op.add_column("mrn_headers", sa.Column("item_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_mrn_headers_item",
        "mrn_headers",
        "material_items",
        ["item_id"],
        ["id"],
    )

    bind = op.get_bind()
    material_items_table = sa.Table(
        "material_items",
        sa.MetaData(),
        sa.Column("id", sa.String(length=36)),
        sa.Column("name", sa.String(length=120)),
        sa.Column("is_active", sa.Boolean()),
    )

    type_rows = bind.execute(
        sa.text("SELECT id, name, is_active FROM material_types")
    ).fetchall()

    item_ids = {}
    seen_names = {}

    for row in type_rows:
        name = (row.name or "").strip() or "Unnamed Item"
        key = name.lower()
        item_id = seen_names.get(key)
        if not item_id:
            item_id = str(uuid.uuid4())
            bind.execute(
                material_items_table.insert().values(
                    id=item_id,
                    name=name,
                    is_active=True if row.is_active is None else bool(row.is_active),
                )
            )
            seen_names[key] = item_id
        item_ids[str(row.id)] = item_id

    mrn_rows = bind.execute(
        sa.text("SELECT id, material_type_id FROM mrn_headers")
    ).fetchall()
    for mrn in mrn_rows:
        mt_id = mrn.material_type_id
        item_id = item_ids.get(str(mt_id))
        if item_id:
            bind.execute(
                sa.text("UPDATE mrn_headers SET item_id = :item_id WHERE id = :id"),
                {"item_id": item_id, "id": mrn.id},
            )

    op.alter_column("mrn_headers", "item_id", existing_type=sa.String(length=36), nullable=False)

    op.drop_constraint("mrn_headers_material_type_id_fkey", "mrn_headers", type_="foreignkey")
    op.drop_constraint("mrn_headers_category_id_fkey", "mrn_headers", type_="foreignkey")
    op.drop_column("mrn_headers", "material_type_id")
    op.drop_column("mrn_headers", "category_id")

    op.drop_table("material_types")
    op.drop_table("material_categories")


def downgrade() -> None:
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
        sa.ForeignKeyConstraint(["category_id"], ["material_categories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category_id", "name", name="uq_material_type_category_name"),
    )

    op.add_column("mrn_headers", sa.Column("category_id", sa.String(length=36), nullable=True))
    op.add_column("mrn_headers", sa.Column("material_type_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "mrn_headers_category_id_fkey",
        "mrn_headers",
        "material_categories",
        ["category_id"],
        ["id"],
    )
    op.create_foreign_key(
        "mrn_headers_material_type_id_fkey",
        "mrn_headers",
        "material_types",
        ["material_type_id"],
        ["id"],
    )

    bind = op.get_bind()
    categories_table = sa.Table(
        "material_categories",
        sa.MetaData(),
        sa.Column("id", sa.String(length=36)),
        sa.Column("name", sa.String(length=80)),
    )
    types_table = sa.Table(
        "material_types",
        sa.MetaData(),
        sa.Column("id", sa.String(length=36)),
        sa.Column("category_id", sa.String(length=36)),
        sa.Column("name", sa.String(length=120)),
        sa.Column("is_active", sa.Boolean()),
    )

    category_id = str(uuid.uuid4())
    bind.execute(categories_table.insert().values(id=category_id, name="Migrated Items"))

    item_rows = bind.execute(
        sa.text("SELECT id, name, is_active FROM material_items")
    ).fetchall()

    type_ids = {}
    for item in item_rows:
        type_id = str(uuid.uuid4())
        bind.execute(
            types_table.insert().values(
                id=type_id,
                category_id=category_id,
                name=item.name,
                is_active=True if item.is_active is None else bool(item.is_active),
            )
        )
        type_ids[str(item.id)] = type_id

    mrn_rows = bind.execute(
        sa.text("SELECT id, item_id FROM mrn_headers")
    ).fetchall()
    for mrn in mrn_rows:
        type_id = type_ids.get(str(mrn.item_id))
        if type_id:
            bind.execute(
                sa.text(
                    "UPDATE mrn_headers SET category_id = :category_id, material_type_id = :material_type_id WHERE id = :id"
                ),
                {"category_id": category_id, "material_type_id": type_id, "id": mrn.id},
            )

    op.alter_column("mrn_headers", "category_id", existing_type=sa.String(length=36), nullable=False)
    op.alter_column("mrn_headers", "material_type_id", existing_type=sa.String(length=36), nullable=False)

    op.drop_constraint("fk_mrn_headers_item", "mrn_headers", type_="foreignkey")
    op.drop_column("mrn_headers", "item_id")
    op.drop_table("material_items")
