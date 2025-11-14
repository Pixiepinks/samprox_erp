"""add performance metrics to responsibilities

Revision ID: 1f9c6b304abc
Revises: fb3e3d7c9a1b
Create Date: 2025-11-14 10:20:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1f9c6b304abc"
down_revision = "fb3e3d7c9a1b"
branch_labels = None
depends_on = None


UNIT_VALUES = [
    "date",
    "time",
    "hours",
    "minutes",
    "days",
    "weeks",
    "months",
    "years",
    "quantity_based",
    "qty",
    "units",
    "pieces",
    "batches",
    "items",
    "parcels",
    "orders",
    "amount_lkr",
    "revenue",
    "cost",
    "expense",
    "profit",
    "savings",
    "margin_pct",
    "number",
    "count",
    "score",
    "frequency",
    "rate",
    "index",
    "kg",
    "tonnes",
    "litres",
    "meters",
    "kwh",
    "rpm",
    "quality_metric",
    "percentage_pct",
    "error_rate_pct",
    "success_rate_pct",
    "defects_per_unit",
    "accuracy_pct",
    "compliance_pct",
    "time_per_unit",
    "units_per_hour",
    "cycle_time",
    "lead_time",
    "customer_count",
    "leads",
    "conversion_pct",
    "tickets_resolved",
    "response_time",
    "milestones",
    "stages",
    "completion_pct",
    "tasks_completed",
    "sla_pct",
]


unit_enum = sa.Enum(*UNIT_VALUES, name="responsibilityperformanceunit")


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade():
    bind = op.get_bind()
    unit_enum.create(bind, checkfirst=True)

    with op.batch_alter_table("responsibility_task") as batch_op:
        if not _has_column("responsibility_task", "perf_uom"):
            batch_op.add_column(
                sa.Column(
                    "perf_uom",
                    unit_enum,
                    nullable=False,
                    server_default="percentage_pct",
                )
            )
        if not _has_column("responsibility_task", "perf_responsible_value"):
            batch_op.add_column(
                sa.Column(
                    "perf_responsible_value",
                    sa.Numeric(18, 4),
                    nullable=False,
                    server_default="0",
                )
            )
        if not _has_column("responsibility_task", "perf_actual_value"):
            batch_op.add_column(
                sa.Column(
                    "perf_actual_value",
                    sa.Numeric(18, 4),
                    nullable=True,
                )
            )
        if not _has_column("responsibility_task", "perf_metric_value"):
            batch_op.add_column(
                sa.Column(
                    "perf_metric_value",
                    sa.Numeric(6, 1),
                    nullable=True,
                )
            )
        if not _has_column("responsibility_task", "perf_input_type"):
            batch_op.add_column(sa.Column("perf_input_type", sa.String(40), nullable=True))

    op.execute(
        """
        UPDATE responsibility_task
        SET
            perf_uom = 'percentage_pct',
            perf_responsible_value = 100,
            perf_actual_value = NULL,
            perf_metric_value = NULL,
            perf_input_type = 'percentage'
        """
    )

    with op.batch_alter_table("responsibility_task") as batch_op:
        batch_op.alter_column(
            "perf_uom",
            existing_type=unit_enum,
            nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "perf_responsible_value",
            existing_type=sa.Numeric(18, 4),
            nullable=False,
            server_default=None,
        )


def downgrade():
    with op.batch_alter_table("responsibility_task") as batch_op:
        if _has_column("responsibility_task", "perf_metric_value"):
            batch_op.drop_column("perf_metric_value")
        if _has_column("responsibility_task", "perf_actual_value"):
            batch_op.drop_column("perf_actual_value")
        if _has_column("responsibility_task", "perf_responsible_value"):
            batch_op.drop_column("perf_responsible_value")
        if _has_column("responsibility_task", "perf_uom"):
            batch_op.drop_column("perf_uom")
        if _has_column("responsibility_task", "perf_input_type"):
            batch_op.drop_column("perf_input_type")

    bind = op.get_bind()
    unit_enum.drop(bind, checkfirst=True)
