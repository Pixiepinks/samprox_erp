"""Update MRN weight logic and constraint

Revision ID: c40f6b5f1de8
Revises: f73970f46c90
Create Date: 2024-09-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c40f6b5f1de8"
down_revision = "f73970f46c90"
branch_labels = None
depends_on = None


def _constraint_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {constraint["name"] for constraint in inspector.get_check_constraints(table_name)}


def upgrade():
    table_name = "mrn_headers"
    constraint_names = _constraint_names(table_name)

    if "ck_mrn_out_weight_greater_than_in" in constraint_names:
        op.drop_constraint("ck_mrn_out_weight_greater_than_in", table_name, type_="check")

    op.execute(
        sa.text(
            """
            UPDATE mrn_headers
            SET weigh_in_weight_kg = weigh_out_weight_kg,
                weigh_out_weight_kg = weigh_in_weight_kg
            WHERE weigh_out_weight_kg IS NOT NULL
              AND weigh_in_weight_kg IS NOT NULL
              AND weigh_out_weight_kg > weigh_in_weight_kg
            """
        )
    )

    if "ck_mrn_first_weight_greater_than_second" not in constraint_names:
        op.create_check_constraint(
            "ck_mrn_first_weight_greater_than_second",
            table_name,
            "weigh_in_weight_kg IS NULL OR weigh_out_weight_kg IS NULL OR weigh_in_weight_kg > weigh_out_weight_kg",
        )


def downgrade():
    table_name = "mrn_headers"
    constraint_names = _constraint_names(table_name)

    if "ck_mrn_first_weight_greater_than_second" in constraint_names:
        op.drop_constraint("ck_mrn_first_weight_greater_than_second", table_name, type_="check")

    op.execute(
        sa.text(
            """
            UPDATE mrn_headers
            SET weigh_in_weight_kg = weigh_out_weight_kg,
                weigh_out_weight_kg = weigh_in_weight_kg
            WHERE weigh_out_weight_kg IS NOT NULL
              AND weigh_in_weight_kg IS NOT NULL
              AND weigh_in_weight_kg > weigh_out_weight_kg
            """
        )
    )

    if "ck_mrn_out_weight_greater_than_in" not in constraint_names:
        op.create_check_constraint(
            "ck_mrn_out_weight_greater_than_in",
            table_name,
            "weigh_out_weight_kg IS NULL OR weigh_in_weight_kg IS NULL OR weigh_out_weight_kg > weigh_in_weight_kg",
        )
