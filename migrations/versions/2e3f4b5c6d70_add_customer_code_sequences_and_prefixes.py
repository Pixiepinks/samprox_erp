"""Add customer code sequences and company prefixes

Revision ID: 2e3f4b5c6d70
Revises: 0e7f3b2a4c10
Create Date: 2026-01-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "2e3f4b5c6d70"
down_revision = "0e7f3b2a4c10"
branch_labels = None
depends_on = None


COMPANY_PREFIXES = {
    "exsol-engineering": "E",
    "rainbows-end-trading": "T",
    "rainbows-industrial": "I",
    "hello-homes": "H",
    "samprox-international": "",
    "samprox": "",
}


COMPANY_NAME_PREFIXES = {
    "Exsol Engineering (Pvt) Ltd": "E",
    "Rainbow Trading (Pvt) Ltd": "T",
    "Rainbows End Trading (Pvt) Ltd": "T",
    "Rainbow Industrial (Pvt) Ltd": "I",
    "Rainbows Industrial (Pvt) Ltd": "I",
    "Hello Homes (Pvt) Ltd": "H",
    "Samprox International (Pvt) Ltd": "",
}


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("company_code_prefix", sa.String(length=4), server_default="", nullable=False),
    )

    op.create_table(
        "customer_code_sequences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("year_yy", sa.String(length=2), nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("company_id", "year_yy", name="uq_customer_code_sequence_company_year"),
    )
    op.create_index("ix_customer_code_sequences_company_id", "customer_code_sequences", ["company_id"])

    with op.batch_alter_table("non_samprox_customers") as batch_op:
        batch_op.alter_column(
            "customer_code",
            existing_type=sa.String(length=6),
            type_=sa.String(length=10),
            nullable=False,
        )

    bind = op.get_bind()
    companies = sa.table(
        "companies",
        sa.column("key", sa.String(length=64)),
        sa.column("name", sa.String(length=255)),
        sa.column("company_code_prefix", sa.String(length=4)),
    )

    for key, prefix in COMPANY_PREFIXES.items():
        bind.execute(
            companies.update()
            .where(companies.c.key == key)
            .values(company_code_prefix=prefix)
        )

    for name, prefix in COMPANY_NAME_PREFIXES.items():
        bind.execute(
            companies.update()
            .where(companies.c.name == name)
            .values(company_code_prefix=prefix)
        )


def downgrade() -> None:
    with op.batch_alter_table("non_samprox_customers") as batch_op:
        batch_op.alter_column(
            "customer_code",
            existing_type=sa.String(length=10),
            type_=sa.String(length=6),
            nullable=False,
        )

    op.drop_index("ix_customer_code_sequences_company_id", table_name="customer_code_sequences")
    op.drop_table("customer_code_sequences")

    with op.batch_alter_table("companies") as batch_op:
        batch_op.drop_column("company_code_prefix")
