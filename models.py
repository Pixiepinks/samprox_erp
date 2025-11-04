import re
import uuid
from datetime import datetime, date
from decimal import Decimal
from enum import Enum

from sqlalchemy import CheckConstraint, UniqueConstraint
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

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.Enum(RoleEnum), nullable=False, default=RoleEnum.production_manager)
    active = db.Column(db.Boolean, default=True)

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
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class MaintenanceJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_code = db.Column(db.String(40), unique=True, nullable=False)
    job_date = db.Column(db.Date, nullable=False, default=date.today)
    title = db.Column(db.String(255), nullable=False)
    priority = db.Column(db.String(20), nullable=False, default="Normal")
    location = db.Column(db.String(120))
    description = db.Column(db.Text)
    expected_completion = db.Column(db.Date)
    status = db.Column(db.Enum(MaintenanceJobStatus), nullable=False, default=MaintenanceJobStatus.NEW)
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

    materials = db.relationship(
        "MaintenanceMaterial",
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
        self.total_cost = total


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
    vehicle_no = db.Column("supplier_name_free", db.String(255))
    qty_ton = db.Column(db.Numeric(12, 3), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    weighing_slip_no = db.Column(db.String(80), nullable=False)
    weigh_in_time = db.Column(db.DateTime(timezone=True), nullable=False)
    weigh_out_time = db.Column(db.DateTime(timezone=True), nullable=False)
    security_officer_name = db.Column(db.String(120), nullable=False)
    authorized_person_name = db.Column(db.String(120), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    supplier = db.relationship("Supplier", back_populates="mrns")
    creator = db.relationship("User")
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
    customer = db.relationship("Customer", backref=db.backref("sales_actuals", cascade="all,delete-orphan"))
