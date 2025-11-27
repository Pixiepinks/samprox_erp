import re
import uuid
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Iterable

from sqlalchemy import CheckConstraint, UniqueConstraint, event, func, select
from sqlalchemy.types import CHAR, TypeDecorator

from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash


class GUID(TypeDecorator):
    """Platform-independent GUID type."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):  # pragma: no cover - SQLAlchemy hook
        #
        # The initial Alembic migrations for this project created UUID columns
        # as VARCHAR(36) fields in the PostgreSQL database.  When SQLAlchemy's
        # PostgreSQL UUID type is used against those columns it coerces bound
        # parameters to ``::UUID`` which leads to ``operator does not exist:
        # character varying = uuid`` errors at runtime.  Always binding the
        # column as ``CHAR(36)`` keeps the ORM layer aligned with the actual
        # database schema while still allowing UUID validation through
        # ``process_bind_param``.
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):  # pragma: no cover - SQLAlchemy hook
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return str(value)
        return str(uuid.UUID(str(value)))

    def process_result_value(self, value, dialect):  # pragma: no cover - SQLAlchemy hook
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))

class RoleEnum(str, Enum):
    admin = "admin"
    production_manager = "production_manager"
    maintenance_manager = "maintenance_manager"
    finance_manager = "finance_manager"
    outside_manager = "outside_manager"
    sales = "sales"


# Explicitly enumerate scoped permissions per role for UI and API guards.
ROLE_PERMISSIONS: dict[RoleEnum, set[str]] = {
    RoleEnum.finance_manager: {
        "responsibility_plan_view",
        "responsibility_plan_edit",
        "responsibility_plan_create",
    },
    RoleEnum.sales: {
        "petty_cash_weekly_travel_claims:view",
        "petty_cash_weekly_travel_claims:create",
        "petty_cash_weekly_travel_claims:edit",
        "petty_cash_weekly_travel_claims:submit",
    }
}


class PettyCashStatus(str, Enum):
    draft = "Draft"
    submitted = "Submitted"
    approved = "Approved"
    rejected = "Rejected"
    paid = "Paid"


class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), nullable=False, unique=True)
    name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:  # pragma: no cover - representation helper
        return f"<Company {self.key}>"


class ChartOfAccount(db.Model):
    __tablename__ = "chart_of_accounts"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=True)
    account_code = db.Column(db.String(20), nullable=False)
    account_name = db.Column(db.String(255), nullable=False)
    ifrs_category = db.Column(db.String(50), nullable=False)
    ifrs_subcategory = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, server_default="1")

    company = db.relationship("Company", backref="chart_of_accounts")

    __table_args__ = (
        UniqueConstraint("company_id", "account_code", name="uq_chart_account_company_code"),
    )


def generate_financial_year_months(fin_year: int) -> list[dict[str, int | str]]:
    """
    Build the ordered months for a financial year (April–March).

    ``fin_year`` represents the starting calendar year (e.g. 2025 for
    April 2025–March 2026).
    """

    months: list[dict[str, int | str]] = []
    for m in range(4, 13):
        months.append(
            {
                "year": fin_year,
                "month": m,
                "label": date(fin_year, m, 1).strftime("%b %Y"),
            }
        )
    for m in range(1, 4):
        next_year = fin_year + 1
        months.append(
            {
                "year": next_year,
                "month": m,
                "label": date(next_year, m, 1).strftime("%b %Y"),
            }
        )
    return months


IFRS_TRIAL_BALANCE_CATEGORIES: dict[str, list[str]] = {
    "Asset": ["Current Asset", "Non-current Asset"],
    "Liability": ["Current Liability", "Non-current Liability"],
    "Equity": ["Share Capital", "Share Premium", "Retained Earnings", "Other Reserves"],
    "Income": ["Operating Revenue", "Other Income", "Finance Income"],
    "Expense": [
        "Cost of Sales",
        "Distribution Expense",
        "Administrative Expense",
        "Staff Cost",
        "Depreciation & Amortisation",
        "Finance Cost",
        "Tax Expense",
        "Other Expense",
    ],
    "OCI": ["Other Comprehensive Income – Gain", "Other Comprehensive Income – Loss"],
}

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.Enum(RoleEnum), nullable=False, default=RoleEnum.production_manager)
    active = db.Column(db.Boolean, default=True)
    company_key = db.Column(db.String(64), nullable=True, index=True)

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

class JobStatus(str, Enum):
    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"

class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.Enum(JobStatus), default=JobStatus.NEW, index=True)
    priority = db.Column(db.String(20), default="Normal")
    location = db.Column(db.String(120))
    expected_completion_date = db.Column(db.Date)
    completed_date = db.Column(db.Date)
    progress_pct_manual = db.Column(db.Integer)  # nullable

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])

    quotation = db.relationship("Quotation", backref="job", uselist=False, cascade="all,delete-orphan")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def progress_pct_auto(self):
        # compute based on labor estimate vs actual
        if not self.quotation or not self.quotation.labor_estimate_hours:
            return 0
        total_hours = sum(le.hours for le in self.labor_entries)
        pct = int(min(round((total_hours / self.quotation.labor_estimate_hours) * 100), 100))
        return pct

    @property
    def progress_pct(self):
        return self.progress_pct_manual if self.progress_pct_manual is not None else self.progress_pct_auto()


class MaintenanceJobStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    FORWARDED_TO_MAINTENANCE = "FORWARDED_TO_MAINTENANCE"
    RETURNED_TO_PRODUCTION = "RETURNED_TO_PRODUCTION"
    NOT_YET_STARTED = "NOT_YET_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_PARTS = "AWAITING_PARTS"
    ON_HOLD = "ON_HOLD"
    TESTING = "TESTING"
    COMPLETED_MAINTENANCE = "COMPLETED_MAINTENANCE"
    COMPLETED_VERIFIED = "COMPLETED_VERIFIED"
    REOPENED = "REOPENED"


maintenance_job_part = db.Table(
    "maintenance_job_part",
    db.Column(
        "job_id",
        db.Integer,
        db.ForeignKey("maintenance_job.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "part_id",
        db.Integer,
        db.ForeignKey("machine_part.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class MaintenanceJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_code = db.Column(db.String(40), unique=True, nullable=False)
    job_date = db.Column(db.Date, nullable=False, default=date.today)
    title = db.Column(db.String(255), nullable=False)
    job_category = db.Column(
        db.String(120), nullable=False, default="Mechanical / Machine Issues"
    )
    priority = db.Column(db.String(20), nullable=False, default="Normal")
    location = db.Column(db.String(120))
    description = db.Column(db.Text)
    expected_completion = db.Column(db.Date)
    status = db.Column(db.String(50), nullable=False, default=MaintenanceJobStatus.SUBMITTED.value)
    prod_email = db.Column(db.String(255))
    maint_email = db.Column(db.String(255))
    prod_submitted_at = db.Column(db.DateTime)
    maint_submitted_at = db.Column(db.DateTime)
    job_started_date = db.Column(db.Date)
    job_finished_date = db.Column(db.Date)
    total_cost = db.Column(db.Numeric(12, 2), default=Decimal("0.00"))
    maintenance_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id])

    asset_id = db.Column(db.Integer, db.ForeignKey("machine_asset.id"))
    asset = db.relationship("MachineAsset", foreign_keys=[asset_id])

    part_id = db.Column(db.Integer, db.ForeignKey("machine_part.id"))
    part = db.relationship("MachinePart", foreign_keys=[part_id])
    parts = db.relationship(
        "MachinePart",
        secondary=maintenance_job_part,
        backref="maintenance_jobs",
    )

    materials = db.relationship(
        "MaintenanceMaterial",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    outsourced_services = db.relationship(
        "MaintenanceOutsourcedService",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    internal_staff_costs = db.relationship(
        "MaintenanceInternalStaffCost",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def recalculate_total_cost(self) -> None:
        total = Decimal("0")
        for material in self.materials:
            cost = material.cost or Decimal("0")
            if not isinstance(cost, Decimal):
                try:
                    cost = Decimal(str(cost))
                except Exception:
                    cost = Decimal("0")
            total += cost
        for service in self.outsourced_services:
            cost = service.cost or Decimal("0")
            if not isinstance(cost, Decimal):
                try:
                    cost = Decimal(str(cost))
                except Exception:
                    cost = Decimal("0")
            total += cost
        for staff_cost in self.internal_staff_costs:
            cost = staff_cost.cost or Decimal("0")
            if not isinstance(cost, Decimal):
                try:
                    cost = Decimal(str(cost))
                except Exception:
                    cost = Decimal("0")
            total += cost
        self.total_cost = total


class ResponsibilityRecurrence(str, Enum):
    """Frequency options for responsibility tasks."""

    DOES_NOT_REPEAT = "does_not_repeat"
    MONDAY_TO_FRIDAY = "monday_to_friday"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ANNUALLY = "annually"
    CUSTOM = "custom"


class ResponsibilityTaskStatus(str, Enum):
    """Lifecycle state for a responsibility task."""

    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ResponsibilityAction(str, Enum):
    """5D action states for a responsibility task."""

    DONE = "done"
    DELEGATED = "delegated"
    DEFERRED = "deferred"
    DISCUSSED = "discussed"
    DELETED = "deleted"


class ResponsibilityPerformanceUnit(str, Enum):
    """Supported units of measure for responsibility performance tracking."""

    DATE = "date"
    TIME = "time"
    HOURS = "hours"
    MINUTES = "minutes"
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"
    QUANTITY_BASED = "quantity_based"
    QTY = "qty"
    UNITS = "units"
    PIECES = "pieces"
    BATCHES = "batches"
    ITEMS = "items"
    PARCELS = "parcels"
    ORDERS = "orders"
    AMOUNT_LKR = "amount_lkr"
    REVENUE = "revenue"
    COST = "cost"
    EXPENSE = "expense"
    PROFIT = "profit"
    SAVINGS = "savings"
    MARGIN_PCT = "margin_pct"
    NUMBER = "number"
    COUNT = "count"
    SCORE = "score"
    FREQUENCY = "frequency"
    RATE = "rate"
    INDEX = "index"
    KG = "kg"
    TONNES = "tonnes"
    LITRES = "litres"
    METERS = "meters"
    KWH = "kwh"
    RPM = "rpm"
    QUALITY_METRIC = "quality_metric"
    PERCENTAGE_PCT = "percentage_pct"
    ERROR_RATE_PCT = "error_rate_pct"
    SUCCESS_RATE_PCT = "success_rate_pct"
    DEFECTS_PER_UNIT = "defects_per_unit"
    ACCURACY_PCT = "accuracy_pct"
    COMPLIANCE_PCT = "compliance_pct"
    TIME_PER_UNIT = "time_per_unit"
    UNITS_PER_HOUR = "units_per_hour"
    CYCLE_TIME = "cycle_time"
    LEAD_TIME = "lead_time"
    CUSTOMER_COUNT = "customer_count"
    LEADS = "leads"
    CONVERSION_PCT = "conversion_pct"
    TICKETS_RESOLVED = "tickets_resolved"
    RESPONSE_TIME = "response_time"
    MILESTONES = "milestones"
    STAGES = "stages"
    COMPLETION_PCT = "completion_pct"
    TASKS_COMPLETED = "tasks_completed"
    SLA_PCT = "sla_pct"


class ResponsibilityTask(db.Model):
    """Represents a scheduled responsibility item for a manager."""

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(20), unique=True, nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    detail = db.Column(db.Text)
    scheduled_for = db.Column(db.Date, nullable=False)
    recurrence = db.Column(
        db.Enum(
            ResponsibilityRecurrence,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ResponsibilityRecurrence.DOES_NOT_REPEAT,
    )
    custom_weekdays = db.Column(db.String(120))
    status = db.Column(
        db.Enum(
            ResponsibilityTaskStatus,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ResponsibilityTaskStatus.PLANNED,
    )
    action = db.Column(
        db.Enum(
            ResponsibilityAction,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ResponsibilityAction.DONE,
    )
    action_notes = db.Column(db.Text)
    recipient_email = db.Column(db.String(255), nullable=False)
    cc_email = db.Column(db.String(255))
    progress = db.Column(db.Integer, nullable=False, default=0)

    perf_uom = db.Column(
        db.Enum(
            ResponsibilityPerformanceUnit,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ResponsibilityPerformanceUnit.PERCENTAGE_PCT,
    )
    perf_responsible_value = db.Column(db.Numeric(18, 4), nullable=False, default=Decimal("0"))
    perf_actual_value = db.Column(db.Numeric(18, 4), nullable=True)
    perf_metric_value = db.Column(db.Numeric(18, 4), nullable=True)
    perf_input_type = db.Column(db.String(40))

    assigner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    assigner = db.relationship("User", foreign_keys=[assigner_id], backref="tasks_created")

    assignee_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    assignee = db.relationship("User", foreign_keys=[assignee_id], backref="tasks_assigned")
    assignee_member_id = db.Column(db.Integer, db.ForeignKey("team_member.id"))
    assignee_member = db.relationship(
        "TeamMember", foreign_keys=[assignee_member_id], backref="responsibilities_assigned"
    )

    delegated_to_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    delegated_to = db.relationship("User", foreign_keys=[delegated_to_id], backref="tasks_delegated")
    delegated_to_member_id = db.Column(db.Integer, db.ForeignKey("team_member.id"))
    delegated_to_member = db.relationship(
        "TeamMember",
        foreign_keys=[delegated_to_member_id],
        backref="responsibilities_delegated",
    )

    delegations = db.relationship(
        "ResponsibilityDelegation",
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def _custom_weekday_values(self) -> list[int]:
        if not self.custom_weekdays:
            return []
        values: list[int] = []
        for part in str(self.custom_weekdays).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                number = int(part)
            except ValueError:
                continue
            if 0 <= number <= 6 and number not in values:
                values.append(number)
        values.sort()
        return values

    @property
    def custom_weekday_list(self) -> list[int]:
        """Return the stored custom weekdays as integers (0 = Monday)."""

        return self._custom_weekday_values()

    def replace_delegations(self, delegations: Iterable["ResponsibilityDelegation"]) -> None:
        """Replace delegations while maintaining backward compatible fields."""

        delegation_list = [
            delegation
            for delegation in delegations
            if getattr(delegation, "delegate_id", None)
            or getattr(delegation, "delegate_member_id", None)
        ]

        def _delegation_key(entry: "ResponsibilityDelegation") -> tuple[str, int] | None:
            member_id = getattr(entry, "delegate_member_id", None)
            if member_id is not None:
                try:
                    return ("member", int(member_id))
                except (TypeError, ValueError):
                    return None
            delegate_id = getattr(entry, "delegate_id", None)
            if delegate_id is not None:
                try:
                    return ("user", int(delegate_id))
                except (TypeError, ValueError):
                    return None
            return None

        existing: dict[tuple[str, int], "ResponsibilityDelegation"] = {}
        for delegation in list(self.delegations or []):
            key = _delegation_key(delegation)
            if key is not None:
                existing[key] = delegation

        updated: list["ResponsibilityDelegation"] = []
        for entry in delegation_list:
            key = _delegation_key(entry)
            if key is None:
                continue
            current = existing.pop(key, None)
            if current is not None:
                current.allocated_value = entry.allocated_value
                current.delegate_id = entry.delegate_id
                current.delegate_member_id = entry.delegate_member_id
                updated.append(current)
            else:
                updated.append(entry)

        for orphan in existing.values():
            self.delegations.remove(orphan)

        self.delegations = updated

        if updated:
            first = updated[0]
            self.delegated_to_id = getattr(first, "delegate_id", None)
            self.delegated_to_member_id = getattr(first, "delegate_member_id", None)
        else:
            self.delegated_to_id = None
            self.delegated_to_member_id = None

    def occurs_on(self, target_date: date) -> bool:
        """Return ``True`` if the task is scheduled for ``target_date``."""

        if not isinstance(target_date, date):
            return False

        if target_date < self.scheduled_for:
            return False

        weekday = target_date.weekday()
        base_weekday = self.scheduled_for.weekday()

        recurrence = self.recurrence or ResponsibilityRecurrence.DOES_NOT_REPEAT

        if recurrence == ResponsibilityRecurrence.DOES_NOT_REPEAT:
            return target_date == self.scheduled_for

        if recurrence == ResponsibilityRecurrence.MONDAY_TO_FRIDAY:
            return weekday <= 4

        if recurrence == ResponsibilityRecurrence.DAILY:
            return True

        if recurrence == ResponsibilityRecurrence.WEEKLY:
            return weekday == base_weekday

        if recurrence == ResponsibilityRecurrence.MONTHLY:
            return target_date.day == self.scheduled_for.day

        if recurrence == ResponsibilityRecurrence.ANNUALLY:
            return (
                target_date.month == self.scheduled_for.month
                and target_date.day == self.scheduled_for.day
            )

        if recurrence == ResponsibilityRecurrence.CUSTOM:
            return weekday in self.custom_weekday_list

        return False

    def update_custom_weekdays(self, weekdays: Iterable[int] | None) -> None:
        """Persist the provided weekday collection."""

        if not weekdays:
            self.custom_weekdays = None
            return

        normalized: list[int] = []
        for value in weekdays:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= number <= 6 and number not in normalized:
                normalized.append(number)

        normalized.sort()
        self.custom_weekdays = ",".join(str(v) for v in normalized) if normalized else None


class ResponsibilityDelegation(db.Model):
    """Represents an allocation of a responsibility to another assignee."""

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer,
        db.ForeignKey("responsibility_task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delegate_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    delegate_member_id = db.Column(
        db.Integer, db.ForeignKey("team_member.id"), nullable=True
    )
    allocated_value = db.Column(db.Numeric(18, 4), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    task = db.relationship("ResponsibilityTask", back_populates="delegations")
    delegate = db.relationship("User")
    delegate_member = db.relationship("TeamMember", foreign_keys=[delegate_member_id])

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "delegate_id",
            "delegate_member_id",
            name="uq_responsibility_delegation",
        ),
    )


@event.listens_for(ResponsibilityTask, "before_insert")
def _assign_responsibility_number(_mapper, connection, target):
    """Automatically assign the next responsibility number."""

    if getattr(target, "number", None):
        return

    result = connection.execute(
        select(func.max(ResponsibilityTask.number))
    ).scalar_one_or_none()

    next_number = 1
    if result:
        try:
            next_number = int(result) + 1
        except (TypeError, ValueError):
            fallback = connection.execute(
                select(func.count(ResponsibilityTask.id))
            ).scalar_one_or_none()
            next_number = (fallback or 0) + 1

    target.number = f"{next_number:04d}"


class MaintenanceMaterial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    maintenance_job_id = db.Column(
        db.Integer,
        db.ForeignKey("maintenance_job.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    material_name = db.Column(db.String(255), nullable=False)
    units = db.Column(db.String(120))
    cost = db.Column(db.Numeric(12, 2))

    job = db.relationship("MaintenanceJob", back_populates="materials")


class MaintenanceOutsourcedService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    maintenance_job_id = db.Column(
        db.Integer,
        db.ForeignKey("maintenance_job.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    supplier_id = db.Column(db.Integer, db.ForeignKey("service_supplier.id"), nullable=False)
    service_date = db.Column(db.Date, nullable=False)
    service_description = db.Column(db.String(255), nullable=False)
    engaged_hours = db.Column(db.Numeric(6, 2))
    cost = db.Column(db.Numeric(12, 2), nullable=False)

    job = db.relationship("MaintenanceJob", back_populates="outsourced_services")
    supplier = db.relationship("ServiceSupplier")


class MaintenanceInternalStaffCost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    maintenance_job_id = db.Column(
        db.Integer,
        db.ForeignKey("maintenance_job.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    employee_id = db.Column(db.Integer, db.ForeignKey("team_member.id"), nullable=False)
    service_date = db.Column(db.Date, nullable=False)
    work_description = db.Column(db.String(255), nullable=False)
    engaged_hours = db.Column(db.Numeric(6, 2))
    hourly_rate = db.Column(db.Numeric(10, 2))
    cost = db.Column(db.Numeric(12, 2), nullable=False)

    job = db.relationship("MaintenanceJob", back_populates="internal_staff_costs")
    employee = db.relationship("TeamMember")

class Quotation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), unique=True, nullable=False)
    labor_estimate_hours = db.Column(db.Float, default=0)
    labor_rate = db.Column(db.Float, default=0)  # per hour
    material_estimate_cost = db.Column(db.Float, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class LaborEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    hours = db.Column(db.Float, nullable=False)
    rate = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(255))
    job = db.relationship("Job", backref=db.backref("labor_entries", cascade="all,delete-orphan"))
    user = db.relationship("User")

class MaterialEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False, index=True)
    item_name = db.Column(db.String(120), nullable=False)
    qty = db.Column(db.Float, nullable=False)
    unit_cost = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(255))
    job = db.relationship("Job", backref=db.backref("material_entries", cascade="all,delete-orphan"))


class Supplier(db.Model):
    __tablename__ = "suppliers"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(255), unique=True, nullable=False)
    primary_phone = db.Column("phone", db.String(40))
    secondary_phone = db.Column(db.String(40))
    category = db.Column(db.String(40))
    vehicle_no_1 = db.Column(db.String(40))
    vehicle_no_2 = db.Column(db.String(40))
    vehicle_no_3 = db.Column(db.String(40))
    supplier_id_no = db.Column(db.String(120))
    supplier_reg_no = db.Column(db.String(20), unique=True, nullable=False)
    credit_period = db.Column(db.String(40))
    email = db.Column(db.String(255))
    address = db.Column(db.Text)
    tax_id = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    mrns = db.relationship("MRNHeader", back_populates="supplier")

    @property
    def phone(self):
        return self.primary_phone

    @phone.setter
    def phone(self, value):
        self.primary_phone = value


class MaterialItem(db.Model):
    __tablename__ = "material_items"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(120), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    mrn_lines = db.relationship(
        "MRNLine",
        back_populates="item",
        passive_deletes=True,
    )


class MRNHeader(db.Model):
    __tablename__ = "mrn_headers"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    mrn_no = db.Column(db.String(60), nullable=False, unique=True)
    date = db.Column(db.Date, nullable=False)
    supplier_id = db.Column(GUID(), db.ForeignKey("suppliers.id"))
    sourcing_type = db.Column(db.String(40), nullable=False, default="Ownsourcing")
    vehicle_no = db.Column("supplier_name_free", db.String(255))
    qty_ton = db.Column(db.Numeric(12, 3), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    weighing_slip_no = db.Column(db.String(80), nullable=False)
    weigh_in_time = db.Column(db.DateTime(timezone=True), nullable=False)
    weigh_out_time = db.Column(db.DateTime(timezone=True), nullable=False)
    security_officer_name = db.Column(db.String(120), nullable=False)
    authorized_person_name = db.Column(db.String(120), nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey("team_member.id", ondelete="SET NULL"))
    helper1_id = db.Column(db.Integer, db.ForeignKey("team_member.id", ondelete="SET NULL"))
    helper2_id = db.Column(db.Integer, db.ForeignKey("team_member.id", ondelete="SET NULL"))
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    supplier = db.relationship("Supplier", back_populates="mrns")
    creator = db.relationship("User")
    driver = db.relationship("TeamMember", foreign_keys=[driver_id])
    helper1 = db.relationship("TeamMember", foreign_keys=[helper1_id])
    helper2 = db.relationship("TeamMember", foreign_keys=[helper2_id])
    items = db.relationship(
        "MRNLine",
        back_populates="mrn",
        cascade="all, delete-orphan",
        order_by="MRNLine.created_at",
    )

    __table_args__ = (
        CheckConstraint("qty_ton > 0", name="ck_mrn_qty_positive"),
        CheckConstraint("amount >= 0", name="ck_mrn_amount_non_negative"),
        CheckConstraint("weigh_out_time >= weigh_in_time", name="ck_mrn_weigh_out_after_in"),
    )


class MRNLine(db.Model):
    __tablename__ = "mrn_lines"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    mrn_id = db.Column(GUID(), db.ForeignKey("mrn_headers.id", ondelete="CASCADE"), nullable=False)
    item_id = db.Column(GUID(), db.ForeignKey("material_items.id"), nullable=False)
    first_weight_kg = db.Column(db.Numeric(12, 3), nullable=False)
    second_weight_kg = db.Column(db.Numeric(12, 3), nullable=False)
    qty_ton = db.Column(db.Numeric(12, 3), nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    wet_factor = db.Column(db.Numeric(6, 3), nullable=False, default=Decimal("1.000"))
    approved_unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    mrn = db.relationship("MRNHeader", back_populates="items")
    item = db.relationship("MaterialItem", back_populates="mrn_lines")

    __table_args__ = (
        CheckConstraint("first_weight_kg >= 0", name="ck_mrn_line_first_weight_non_negative"),
        CheckConstraint("second_weight_kg >= 0", name="ck_mrn_line_second_weight_non_negative"),
        CheckConstraint("first_weight_kg > second_weight_kg", name="ck_mrn_line_weight_order"),
        CheckConstraint("qty_ton > 0", name="ck_mrn_line_qty_positive"),
        CheckConstraint("unit_price >= 0", name="ck_mrn_line_unit_price_non_negative"),
        CheckConstraint("wet_factor >= 0", name="ck_mrn_line_wet_factor_non_negative"),
        CheckConstraint(
            "approved_unit_price >= 0",
            name="ck_mrn_line_approved_unit_price_non_negative",
        ),
        CheckConstraint("amount >= 0", name="ck_mrn_line_amount_non_negative"),
    )

class MachineAsset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(60), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(120))
    location = db.Column(db.String(120))
    manufacturer = db.Column(db.String(120))
    model_number = db.Column(db.String(120))
    serial_number = db.Column(db.String(120))
    installed_on = db.Column(db.Date)
    status = db.Column(db.String(40))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MachinePart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("machine_asset.id"), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    part_number = db.Column(db.String(120))
    description = db.Column(db.Text)
    expected_life_hours = db.Column(db.Integer)
    notes = db.Column(db.Text)
    asset = db.relationship(
        "MachineAsset",
        backref=db.backref("parts", cascade="all,delete-orphan", order_by="MachinePart.name"),
    )

    @classmethod
    def generate_part_number(cls):
        prefix = "P-"
        max_number = 0
        numbers = (
            db.session.query(cls.part_number)
            .filter(cls.part_number.isnot(None))
            .filter(cls.part_number.ilike(f"{prefix}%"))
            .all()
        )
        for (value,) in numbers:
            if not value:
                continue
            match = re.match(r"^P-(\d+)$", value.strip(), re.IGNORECASE)
            if match:
                max_number = max(max_number, int(match.group(1)))
        return f"{prefix}{max_number + 1:03d}"


class MachinePartReplacement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    part_id = db.Column(db.Integer, db.ForeignKey("machine_part.id"), nullable=False, index=True)
    replaced_on = db.Column(db.Date, nullable=False)
    replaced_by = db.Column(db.String(120))
    reason = db.Column(db.String(255))
    notes = db.Column(db.Text)
    part = db.relationship(
        "MachinePart",
        backref=db.backref(
            "replacement_history",
            cascade="all,delete-orphan",
            order_by="MachinePartReplacement.replaced_on.desc()",
        ),
    )


class MachineIdleEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("machine_asset.id"), nullable=False, index=True)
    started_at = db.Column(db.DateTime, nullable=False)
    ended_at = db.Column(db.DateTime)
    reason = db.Column(db.String(255))
    secondary_reason = db.Column(db.String(255))
    notes = db.Column(db.Text)
    asset = db.relationship("MachineAsset", backref=db.backref("idle_events", cascade="all,delete-orphan"))

    @property
    def duration_minutes(self):
        if not self.started_at:
            return None
        end_time = self.ended_at or datetime.utcnow()
        delta = end_time - self.started_at
        return int(delta.total_seconds() // 60)


class ServiceSupplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    contact_person = db.Column(db.String(120))
    phone = db.Column(db.String(60))
    email = db.Column(db.String(120))
    services_offered = db.Column(db.String(255))
    preferred_assets = db.Column(db.String(255))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TeamMemberStatus(str, Enum):
    ACTIVE = "Active"
    ON_LEAVE = "On Leave"
    INACTIVE = "Inactive"

    @property
    def label(self) -> str:
        """Return a UI friendly label for the enum value."""

        return self.value


class PayCategory(str, Enum):
    OFFICE = "Office"
    FACTORY = "Factory"
    CASUAL = "Casual"
    LOADING = "Loading"
    TRANSPORT = "Transport"
    MAINTENANCE = "Maintenance"
    OTHER = "Other"

    @property
    def label(self) -> str:
        """Return a UI friendly label for the enum value."""

        return self.value


class TeamMember(db.Model):
    __tablename__ = "team_member"

    id = db.Column(db.Integer, primary_key=True)
    reg_number = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    nickname = db.Column(db.String(120))
    epf = db.Column(db.String(120))
    position = db.Column(db.String(120))
    pay_category = db.Column(
        db.Enum(
            PayCategory,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            name="teammemberpaycategory",
            validate_strings=True,
        ),
        nullable=False,
        default=PayCategory.OFFICE,
    )
    join_date = db.Column(db.Date, nullable=False)
    status = db.Column(
        db.Enum(
            TeamMemberStatus,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            name="teammemberstatus",
            validate_strings=True,
        ),
        nullable=False,
        default=TeamMemberStatus.ACTIVE,
    )
    image_url = db.Column(db.String(500))
    personal_detail = db.Column(db.Text)
    assignments = db.Column(db.Text)
    training_records = db.Column(db.Text)
    employment_log = db.Column(db.Text)
    files = db.Column(db.Text)
    assets = db.Column(db.Text)
    bank_account_name = db.Column(db.String(200))
    bank_name = db.Column(db.String(200))
    branch_name = db.Column(db.String(200))
    bank_account_number = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def update_from_payload(self, data: dict[str, str]):
        """Update mutable fields based on a payload."""

        name = (data.get("name") or "").strip()
        if name:
            self.name = name

        nickname = (data.get("nickname") or "").strip()
        self.nickname = nickname or None

        epf = (data.get("epf") or "").strip()
        self.epf = epf or None

        position = (data.get("position") or "").strip()
        self.position = position or None

        pay_category = (data.get("payCategory") or "").strip()
        if pay_category:
            try:
                self.pay_category = PayCategory(pay_category)
            except ValueError:
                pass

        image = (data.get("image") or "").strip()
        self.image_url = image or None

        personal_detail = (data.get("personalDetail") or "").strip()
        self.personal_detail = personal_detail or None

        assignments = (data.get("assignments") or "").strip()
        self.assignments = assignments or None

        training_records = (data.get("trainingRecords") or "").strip()
        self.training_records = training_records or None

        employment_log = (data.get("employmentLog") or "").strip()
        self.employment_log = employment_log or None

        files = (data.get("files") or "").strip()
        self.files = files or None

        bank_account_name = (data.get("bankAccountName") or "").strip()
        self.bank_account_name = bank_account_name or None

        bank_name = (data.get("bankName") or "").strip()
        self.bank_name = bank_name or None

        branch_name = (data.get("branchName") or "").strip()
        self.branch_name = branch_name or None

        account_number = (data.get("bankAccountNumber") or "").strip()
        self.bank_account_number = account_number or None

        assets = (data.get("assets") or "").strip()
        self.assets = assets or None


class TeamAttendanceRecord(db.Model):
    """Store per-day attendance entries for a team member and month."""

    __tablename__ = "team_attendance_record"
    __table_args__ = (
        UniqueConstraint("team_member_id", "month", name="uq_team_attendance_record_member_month"),
    )

    id = db.Column(db.Integer, primary_key=True)
    team_member_id = db.Column(db.Integer, db.ForeignKey("team_member.id"), nullable=False, index=True)
    month = db.Column(db.String(7), nullable=False)  # YYYY-MM
    entries = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    team_member = db.relationship(
        "TeamMember",
        backref=db.backref("attendance_records", cascade="all,delete-orphan"),
    )


class TeamLeaveBalance(db.Model):
    """Store monthly leave utilisation and balances for a team member."""

    __tablename__ = "team_leave_balance"
    __table_args__ = (
        UniqueConstraint("team_member_id", "month", name="uq_team_leave_balance_member_month"),
    )

    id = db.Column(db.Integer, primary_key=True)
    team_member_id = db.Column(db.Integer, db.ForeignKey("team_member.id"), nullable=False, index=True)
    month = db.Column(db.String(7), nullable=False)  # YYYY-MM
    work_days = db.Column(db.Integer, nullable=False, default=0)
    no_pay_days = db.Column(db.Integer, nullable=False, default=0)
    annual_brought_forward = db.Column(db.Integer, nullable=False, default=0)
    annual_taken = db.Column(db.Integer, nullable=False, default=0)
    annual_balance = db.Column(db.Integer, nullable=False, default=0)
    medical_brought_forward = db.Column(db.Integer, nullable=False, default=0)
    medical_taken = db.Column(db.Integer, nullable=False, default=0)
    medical_balance = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    team_member = db.relationship(
        "TeamMember",
        backref=db.backref("leave_balances", cascade="all,delete-orphan"),
    )


class TeamSalaryRecord(db.Model):
    """Store monthly salary breakdowns for a team member."""

    __tablename__ = "team_salary_record"
    __table_args__ = (
        UniqueConstraint("team_member_id", "month", name="uq_team_salary_record_member_month"),
    )

    id = db.Column(db.Integer, primary_key=True)
    team_member_id = db.Column(db.Integer, db.ForeignKey("team_member.id"), nullable=False, index=True)
    month = db.Column(db.String(7), nullable=False)  # YYYY-MM
    components = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    team_member = db.relationship(
        "TeamMember",
        backref=db.backref("salary_records", cascade="all,delete-orphan"),
    )


class TeamWorkCalendarDay(db.Model):
    """Store work day overrides for the workforce calendar."""

    __tablename__ = "team_work_calendar_day"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True, index=True)
    is_work_day = db.Column(db.Boolean, nullable=False, default=True)
    holiday_name = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class DailyProductionEntry(db.Model):
    __table_args__ = (
        db.UniqueConstraint("date", "asset_id", "hour_no", name="uq_daily_production_entry_day_asset_hour"),
    )

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("machine_asset.id"), nullable=False, index=True)
    hour_no = db.Column(db.Integer, nullable=False)
    quantity_tons = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    asset = db.relationship(
        "MachineAsset",
        backref=db.backref(
            "daily_production_entries",
            cascade="all,delete-orphan",
            order_by="DailyProductionEntry.hour_no",
        ),
    )

    def __repr__(self):
        return (
            f"<DailyProductionEntry date={self.date} asset_id={self.asset_id} "
            f"hour={self.hour_no} qty={self.quantity_tons}>"
        )


class BriquetteMixEntry(db.Model):
    """Store per-day briquette material mix and cost calculations."""

    __tablename__ = "briquette_mix_entries"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True, index=True)
    dry_factor = db.Column(db.Numeric(10, 4))
    sawdust_qty_ton = db.Column(db.Numeric(12, 3), nullable=False, default=Decimal("0.000"))
    wood_shaving_qty_ton = db.Column(db.Numeric(12, 3), nullable=False, default=Decimal("0.000"))
    wood_powder_qty_ton = db.Column(db.Numeric(12, 3), nullable=False, default=Decimal("0.000"))
    peanut_husk_qty_ton = db.Column(db.Numeric(12, 3), nullable=False, default=Decimal("0.000"))
    fire_cut_qty_ton = db.Column(db.Numeric(12, 3), nullable=False, default=Decimal("0.000"))
    total_material_cost = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    unit_cost_per_kg = db.Column(db.Numeric(12, 4), nullable=False, default=Decimal("0.0000"))
    total_output_kg = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0.000"))
    cost_breakdown = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<BriquetteMixEntry date={self.date} total_cost={self.total_material_cost}>"


class ProductionForecastEntry(db.Model):
    __table_args__ = (
        db.UniqueConstraint(
            "date",
            "asset_id",
            name="uq_production_forecast_entry_day_asset",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("machine_asset.id"),
        nullable=False,
        index=True,
    )
    forecast_tons = db.Column(db.Float, nullable=False, default=0)
    forecast_hours = db.Column(db.Float, nullable=False, default=0)
    average_hourly_production = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    asset = db.relationship(
        "MachineAsset",
        backref=db.backref(
            "production_forecast_entries",
            cascade="all,delete-orphan",
            order_by="ProductionForecastEntry.date",
        ),
    )

    def __repr__(self):
        return (
            f"<ProductionForecastEntry date={self.date} asset_id={self.asset_id} "
            f"forecast={self.forecast_tons} hours={self.forecast_hours} "
            f"average={self.average_hourly_production}>"
        )


class CustomerCategory(str, Enum):
    plantation = "plantation"
    industrial = "industrial"


class CustomerCreditTerm(str, Enum):
    cash = "cash"
    days14 = "14_days"
    days30 = "30_days"
    days45 = "45_days"
    days60 = "60_days"


class CustomerTransportMode(str, Enum):
    samprox_lorry = "samprox_lorry"
    customer_lorry = "customer_lorry"


class CustomerType(str, Enum):
    regular = "regular"
    seasonal = "seasonal"


class CustomerPurchaseOrderStatus(str, Enum):
    draft = "Draft"
    confirmed = "Confirmed"
    partially_delivered = "Partially Delivered"
    fully_delivered = "Fully Delivered"
    cancelled = "Cancelled"


def _enum_values(enum_cls):
    return [member.value for member in enum_cls]


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    category = db.Column(db.Enum(CustomerCategory), nullable=False)
    credit_term = db.Column(
        db.Enum(
            CustomerCreditTerm,
            values_callable=_enum_values,
            name="customer_credit_term",
        ),
        nullable=False,
    )
    transport_mode = db.Column(db.Enum(CustomerTransportMode), nullable=False)
    customer_type = db.Column(db.Enum(CustomerType), nullable=False)
    sales_coordinator_name = db.Column(db.String(120), nullable=False)
    sales_coordinator_phone = db.Column(db.String(50), nullable=False)
    store_keeper_name = db.Column(db.String(120), nullable=False)
    store_keeper_phone = db.Column(db.String(50), nullable=False)
    payment_coordinator_name = db.Column(db.String(120), nullable=False)
    payment_coordinator_phone = db.Column(db.String(50), nullable=False)
    special_note = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def code(self):
        if self.id is None:
            return None
        return f"{self.id:05d}"


class SalesForecastEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False, default=0.0)
    quantity_tons = db.Column(db.Float, nullable=False, default=0.0)
    note = db.Column(db.String(255))
    customer = db.relationship("Customer", backref=db.backref("sales_forecasts", cascade="all,delete-orphan"))

    @classmethod
    def for_month(cls, customer_id: int, target_date: date):
        first_day = target_date.replace(day=1)
        if first_day.month == 12:
            next_month = date(first_day.year + 1, 1, 1)
        else:
            next_month = date(first_day.year, first_day.month + 1, 1)
        return (
            cls.query.filter_by(customer_id=customer_id)
            .filter(cls.date >= first_day, cls.date < next_month)
            .order_by(cls.date.asc())
        )


class SalesActualEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False, default=0.0)
    quantity_tons = db.Column(db.Float, nullable=False, default=0.0)
    reference = db.Column(db.String(120))
    delivery_note_number = db.Column(db.String(120))
    weigh_slip_number = db.Column(db.String(120))
    loader1_id = db.Column(db.Integer, db.ForeignKey("team_member.id"))
    loader2_id = db.Column(db.Integer, db.ForeignKey("team_member.id"))
    loader3_id = db.Column(db.Integer, db.ForeignKey("team_member.id"))
    vehicle_number = db.Column(db.String(40))
    driver_id = db.Column(db.Integer, db.ForeignKey("team_member.id"))
    helper1_id = db.Column(db.Integer, db.ForeignKey("team_member.id"))
    helper2_id = db.Column(db.Integer, db.ForeignKey("team_member.id"))
    mileage_km = db.Column(db.Float)
    loader1 = db.relationship("TeamMember", foreign_keys=[loader1_id])
    loader2 = db.relationship("TeamMember", foreign_keys=[loader2_id])
    loader3 = db.relationship("TeamMember", foreign_keys=[loader3_id])
    driver = db.relationship("TeamMember", foreign_keys=[driver_id])
    helper1 = db.relationship("TeamMember", foreign_keys=[helper1_id])
    helper2 = db.relationship("TeamMember", foreign_keys=[helper2_id])
    customer = db.relationship("Customer", backref=db.backref("sales_actuals", cascade="all,delete-orphan"))


class CustomerPurchaseOrder(db.Model):
    __tablename__ = "customer_purchase_orders"

    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(40), unique=True, nullable=False)
    po_date = db.Column(db.Date, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False, index=True)
    customer_reference = db.Column(db.String(120))
    delivery_address = db.Column(db.Text)
    delivery_date = db.Column(db.Date)
    payment_terms = db.Column(db.String(120))
    sales_rep_id = db.Column(db.Integer, db.ForeignKey("team_member.id"))
    contact_person = db.Column(db.String(120))
    contact_phone = db.Column(db.String(80))
    contact_email = db.Column(db.String(120))
    subtotal_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    discount_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    vat_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    other_charges = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    grand_total = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    advance_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    outstanding_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    status = db.Column(
        db.Enum(
            CustomerPurchaseOrderStatus,
            values_callable=_enum_values,
            name="customer_purchase_order_status",
        ),
        nullable=False,
        default=CustomerPurchaseOrderStatus.draft,
    )
    internal_notes = db.Column(db.Text)
    customer_notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False, index=True)

    customer = db.relationship("Customer", backref=db.backref("customer_purchase_orders", cascade="all, delete-orphan"))
    sales_rep = db.relationship("TeamMember")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    items = db.relationship(
        "CustomerPurchaseOrderItem",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CustomerPurchaseOrderItem(db.Model):
    __tablename__ = "customer_purchase_order_items"

    id = db.Column(db.Integer, primary_key=True)
    customer_po_id = db.Column(
        db.Integer,
        db.ForeignKey("customer_purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = db.Column(GUID(), db.ForeignKey("material_items.id"), nullable=False)
    item_code = db.Column(db.String(120), nullable=False)
    item_name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    qty_ordered = db.Column(db.Numeric(14, 3), nullable=False)
    unit = db.Column(db.String(40), nullable=False)
    unit_price = db.Column(db.Numeric(14, 2), nullable=False)
    discount_percent = db.Column(db.Numeric(6, 2), nullable=False, default=Decimal("0.00"))
    line_total = db.Column(db.Numeric(14, 2), nullable=False)
    qty_delivered = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0.000"))
    qty_balance = db.Column(db.Numeric(14, 3), nullable=False, default=Decimal("0.000"))

    purchase_order = db.relationship("CustomerPurchaseOrder", back_populates="items")
    item = db.relationship("MaterialItem")


class PettyCashWeeklyClaim(db.Model):
    __tablename__ = "petty_cash_weekly_claims"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    employee_name = db.Column(db.String(255), nullable=False)
    company_id = db.Column(db.String(64), index=True)
    sheet_no = db.Column(db.String(64), nullable=False, unique=True)
    week_start_date = db.Column(db.Date, nullable=False, index=True)
    week_end_date = db.Column(db.Date, nullable=False)
    vehicle_no = db.Column(db.String(100))
    area_visited = db.Column(db.Text)
    monday_morning_odo = db.Column(db.Numeric(14, 2), nullable=True, default=None)
    friday_evening_odo = db.Column(db.Numeric(14, 2), nullable=True, default=None)
    status = db.Column(
        db.Enum(
            PettyCashStatus,
            values_callable=_enum_values,
            name="pettycashstatus",
        ),
        nullable=False,
        default=PettyCashStatus.draft,
    )
    total_expenses = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship("User", foreign_keys=[employee_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    lines = db.relationship(
        "PettyCashWeeklyLine",
        back_populates="claim",
        cascade="all, delete-orphan",
        order_by="PettyCashWeeklyLine.line_order",
    )

    def recalculate_totals(self) -> None:
        total = Decimal("0")
        for line in self.lines:
            line_total = line.row_total or Decimal("0")
            if not isinstance(line_total, Decimal):
                try:
                    line_total = Decimal(str(line_total))
                except Exception:
                    line_total = Decimal("0")
            total += line_total
        self.total_expenses = total


class PettyCashWeeklyLine(db.Model):
    __tablename__ = "petty_cash_weekly_lines"

    id = db.Column(db.Integer, primary_key=True)
    claim_id = db.Column(
        db.Integer,
        db.ForeignKey("petty_cash_weekly_claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_order = db.Column(db.Integer, nullable=False, index=True)
    expense_type = db.Column(db.String(255))
    mon_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    tue_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    wed_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    thu_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    fri_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    sat_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    sun_amount = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    row_total = db.Column(db.Numeric(14, 2), nullable=False, default=Decimal("0.00"))

    claim = db.relationship("PettyCashWeeklyClaim", back_populates="lines")

    def recalculate_total(self) -> None:
        amounts = [
            self.mon_amount,
            self.tue_amount,
            self.wed_amount,
            self.thu_amount,
            self.fri_amount,
            self.sat_amount,
            self.sun_amount,
        ]
        total = Decimal("0")
        for amount in amounts:
            value = amount or Decimal("0")
            if not isinstance(value, Decimal):
                try:
                    value = Decimal(str(value))
                except Exception:
                    value = Decimal("0")
            total += value
        self.row_total = total


class FinancialStatementLine(db.Model):
    __tablename__ = "financial_statement_lines"

    id = db.Column(db.Integer, primary_key=True)
    statement_type = db.Column(db.String(50), nullable=False)
    line_key = db.Column(db.String(100), nullable=False)
    label = db.Column(db.String(255), nullable=False)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    level = db.Column(db.Integer, nullable=False, default=0)
    is_section = db.Column(db.Boolean, nullable=False, default=False)
    is_subtotal = db.Column(db.Boolean, nullable=False, default=False)
    is_calculated = db.Column(db.Boolean, nullable=False, default=False)

    __table_args__ = (
        db.UniqueConstraint("statement_type", "line_key", name="uq_statement_line_key"),
    )


class FinancialStatementValue(db.Model):
    __tablename__ = "financial_statement_values"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    statement_type = db.Column(db.String(50), nullable=False)
    line_key = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Numeric(18, 2), nullable=False, default=Decimal("0"))

    company = db.relationship("Company", backref="financial_values")

    __table_args__ = (
        db.UniqueConstraint(
            "company_id",
            "year",
            "month",
            "statement_type",
            "line_key",
            name="uq_financial_statement_value",
        ),
    )


class FinancialTrialBalanceLine(db.Model):
    __tablename__ = "financial_trial_balance_lines"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    financial_year = db.Column(db.String(9), nullable=False)
    month_index = db.Column(db.SmallInteger, nullable=False)
    calendar_year = db.Column(db.Integer, nullable=False)
    calendar_month = db.Column(db.Integer, nullable=False)
    account_code = db.Column(db.String(50), nullable=False)
    account_name = db.Column(db.String(255), nullable=False)
    ifrs_category = db.Column(db.String(50), nullable=False)
    ifrs_subcategory = db.Column(db.String(100), nullable=False)
    debit_amount = db.Column(db.Numeric(18, 2), nullable=False, default=Decimal("0"))
    credit_amount = db.Column(db.Numeric(18, 2), nullable=False, default=Decimal("0"))
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    company = db.relationship("Company", backref="trial_balance_lines")

    __table_args__ = (
        db.UniqueConstraint(
            "company_id",
            "financial_year",
            "month_index",
            "account_code",
            "ifrs_category",
            "ifrs_subcategory",
            name="uq_trial_balance_month_account",
        ),
    )
