"""Expand supplier details"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8f2a7c2b1c45"
down_revision = "7d4694ff3d6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("secondary_phone", sa.String(length=40), nullable=True))
    op.add_column("suppliers", sa.Column("category", sa.String(length=40), nullable=True))
    op.add_column("suppliers", sa.Column("vehicle_no_1", sa.String(length=40), nullable=True))
    op.add_column("suppliers", sa.Column("vehicle_no_2", sa.String(length=40), nullable=True))
    op.add_column("suppliers", sa.Column("vehicle_no_3", sa.String(length=40), nullable=True))
    op.add_column("suppliers", sa.Column("supplier_id_no", sa.String(length=120), nullable=True))
    op.add_column("suppliers", sa.Column("supplier_reg_no", sa.String(length=20), nullable=True))
    op.add_column("suppliers", sa.Column("credit_period", sa.String(length=40), nullable=True))

    bind = op.get_bind()
    supplier_rows = bind.execute(
        sa.text("SELECT id FROM suppliers ORDER BY created_at ASC, id ASC")
    ).fetchall()
    for index, row in enumerate(supplier_rows, start=1):
        registration_no = f"SR{index:04d}"
        bind.execute(
            sa.text(
                "UPDATE suppliers SET supplier_reg_no = :registration_no WHERE id = :supplier_id"
            ),
            {"registration_no": registration_no, "supplier_id": str(row[0])},
        )

    op.alter_column(
        "suppliers",
        "supplier_reg_no",
        existing_type=sa.String(length=20),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_suppliers_supplier_reg_no", "suppliers", ["supplier_reg_no"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_suppliers_supplier_reg_no", "suppliers", type_="unique")
    op.drop_column("suppliers", "credit_period")
    op.drop_column("suppliers", "supplier_reg_no")
    op.drop_column("suppliers", "supplier_id_no")
    op.drop_column("suppliers", "vehicle_no_3")
    op.drop_column("suppliers", "vehicle_no_2")
    op.drop_column("suppliers", "vehicle_no_1")
    op.drop_column("suppliers", "category")
    op.drop_column("suppliers", "secondary_phone")
