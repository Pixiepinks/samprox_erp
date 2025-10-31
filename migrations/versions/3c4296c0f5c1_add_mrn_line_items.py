"""add mrn line items table

Revision ID: 3c4296c0f5c1
Revises: b88d1c2d9f3e
Create Date: 2024-12-01 00:00:00.000000
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from alembic import op
import sqlalchemy as sa


revision = "3c4296c0f5c1"
down_revision = "b88d1c2d9f3e"
branch_labels = None
depends_on = None


def _constraint_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {constraint["name"] for constraint in inspector.get_check_constraints(table_name)}


def _foreign_key_names(table_name: str, column: str) -> list[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    names: list[str] = []
    for fk in inspector.get_foreign_keys(table_name):
        if column in fk.get("constrained_columns", []):
            names.append(fk["name"])
    return names


def upgrade() -> None:
    op.create_table(
        "mrn_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mrn_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("first_weight_kg", sa.Numeric(12, 3), nullable=False),
        sa.Column("second_weight_kg", sa.Numeric(12, 3), nullable=False),
        sa.Column("qty_ton", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("wet_factor", sa.Numeric(6, 3), nullable=False, server_default="1.000"),
        sa.Column("approved_unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["mrn_id"], ["mrn_headers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["material_items.id"]),
        sa.CheckConstraint("first_weight_kg >= 0", name="ck_mrn_line_first_weight_non_negative"),
        sa.CheckConstraint("second_weight_kg >= 0", name="ck_mrn_line_second_weight_non_negative"),
        sa.CheckConstraint("first_weight_kg > second_weight_kg", name="ck_mrn_line_weight_order"),
        sa.CheckConstraint("qty_ton > 0", name="ck_mrn_line_qty_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_mrn_line_unit_price_non_negative"),
        sa.CheckConstraint("wet_factor >= 0", name="ck_mrn_line_wet_factor_non_negative"),
        sa.CheckConstraint(
            "approved_unit_price >= 0",
            name="ck_mrn_line_approved_unit_price_non_negative",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_mrn_line_amount_non_negative"),
        sa.PrimaryKeyConstraint("id"),
    )

    bind = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(bind=bind, only=["mrn_headers", "mrn_lines"])
    mrn_headers = metadata.tables["mrn_headers"]
    mrn_lines = metadata.tables["mrn_lines"]

    select_columns = [
        mrn_headers.c.id,
        mrn_headers.c.item_id,
        mrn_headers.c.weigh_in_weight_kg,
        mrn_headers.c.weigh_out_weight_kg,
        mrn_headers.c.qty_ton,
        mrn_headers.c.unit_price,
        mrn_headers.c.wet_factor,
        mrn_headers.c.approved_unit_price,
        mrn_headers.c.amount,
        mrn_headers.c.created_at,
        mrn_headers.c.updated_at,
    ]

    existing_rows = bind.execute(sa.select(*select_columns)).fetchall()

    insert_stmt = mrn_lines.insert()
    for row in existing_rows:
        if row.item_id is None:
            continue
        first_weight = row.weigh_in_weight_kg
        second_weight = row.weigh_out_weight_kg
        qty = row.qty_ton or Decimal("0")
        unit_price = row.unit_price or Decimal("0")
        wet_factor = row.wet_factor or Decimal("1.000")
        approved_unit = row.approved_unit_price or Decimal("0")
        amount = row.amount or Decimal("0")

        bind.execute(
            insert_stmt,
            {
                "id": str(uuid.uuid4()),
                "mrn_id": row.id,
                "item_id": row.item_id,
                "first_weight_kg": first_weight or Decimal("0"),
                "second_weight_kg": second_weight or Decimal("0"),
                "qty_ton": qty,
                "unit_price": unit_price,
                "wet_factor": wet_factor,
                "approved_unit_price": approved_unit,
                "amount": amount,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            },
        )

    constraint_names = _constraint_names("mrn_headers")
    for name in (
        "ck_mrn_unit_price_non_negative",
        "ck_mrn_wet_factor_non_negative",
        "ck_mrn_approved_unit_price_non_negative",
        "ck_mrn_first_weight_greater_than_second",
    ):
        if name in constraint_names:
            op.drop_constraint(name, "mrn_headers", type_="check")

    for fk_name in _foreign_key_names("mrn_headers", "item_id"):
        op.drop_constraint(fk_name, "mrn_headers", type_="foreignkey")

    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("mrn_headers")}
    with op.batch_alter_table("mrn_headers") as batch_op:
        for column in (
            "item_id",
            "unit_price",
            "wet_factor",
            "approved_unit_price",
            "weigh_in_weight_kg",
            "weigh_out_weight_kg",
        ):
            if column in existing_columns:
                batch_op.drop_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("mrn_headers")}

    with op.batch_alter_table("mrn_headers") as batch_op:
        if "item_id" not in existing_columns:
            batch_op.add_column(sa.Column("item_id", sa.String(length=36), nullable=True))
        if "unit_price" not in existing_columns:
            batch_op.add_column(sa.Column("unit_price", sa.Numeric(12, 2), nullable=True))
        if "wet_factor" not in existing_columns:
            batch_op.add_column(
                sa.Column("wet_factor", sa.Numeric(6, 3), nullable=True, server_default="1.000")
            )
        if "approved_unit_price" not in existing_columns:
            batch_op.add_column(sa.Column("approved_unit_price", sa.Numeric(12, 2), nullable=True))
        if "weigh_in_weight_kg" not in existing_columns:
            batch_op.add_column(sa.Column("weigh_in_weight_kg", sa.Numeric(12, 3), nullable=True))
        if "weigh_out_weight_kg" not in existing_columns:
            batch_op.add_column(sa.Column("weigh_out_weight_kg", sa.Numeric(12, 3), nullable=True))

    metadata = sa.MetaData()
    metadata.reflect(bind=bind, only=["mrn_headers", "mrn_lines"])
    mrn_headers = metadata.tables["mrn_headers"]
    mrn_lines = metadata.tables["mrn_lines"]

    rows = bind.execute(
        sa.select(
            mrn_lines.c.mrn_id,
            mrn_lines.c.item_id,
            mrn_lines.c.first_weight_kg,
            mrn_lines.c.second_weight_kg,
            mrn_lines.c.unit_price,
            mrn_lines.c.wet_factor,
            mrn_lines.c.approved_unit_price,
        ).order_by(mrn_lines.c.created_at)
    ).fetchall()

    seen: dict[str, sa.Row] = {}
    for row in rows:
        if row.mrn_id in seen:
            continue
        seen[row.mrn_id] = row

    for mrn_id, row in seen.items():
        bind.execute(
            mrn_headers.update()
            .where(mrn_headers.c.id == mrn_id)
            .values(
                item_id=row.item_id,
                weigh_in_weight_kg=row.first_weight_kg,
                weigh_out_weight_kg=row.second_weight_kg,
                unit_price=row.unit_price,
                wet_factor=row.wet_factor,
                approved_unit_price=row.approved_unit_price,
            )
        )

    constraint_names = _constraint_names("mrn_headers")
    if "ck_mrn_unit_price_non_negative" not in constraint_names:
        op.create_check_constraint(
            "ck_mrn_unit_price_non_negative",
            "mrn_headers",
            "unit_price >= 0",
        )
    if "ck_mrn_wet_factor_non_negative" not in constraint_names:
        op.create_check_constraint(
            "ck_mrn_wet_factor_non_negative",
            "mrn_headers",
            "wet_factor >= 0",
        )
    if "ck_mrn_approved_unit_price_non_negative" not in constraint_names:
        op.create_check_constraint(
            "ck_mrn_approved_unit_price_non_negative",
            "mrn_headers",
            "approved_unit_price >= 0",
        )
    if "ck_mrn_first_weight_greater_than_second" not in constraint_names:
        op.create_check_constraint(
            "ck_mrn_first_weight_greater_than_second",
            "mrn_headers",
            "weigh_in_weight_kg IS NULL OR weigh_out_weight_kg IS NULL OR weigh_in_weight_kg > weigh_out_weight_kg",
        )

    fk_names = _foreign_key_names("mrn_headers", "item_id")
    if not fk_names:
        op.create_foreign_key(
            None,
            "mrn_headers",
            "material_items",
            ["item_id"],
            ["id"],
        )

    op.drop_table("mrn_lines")
