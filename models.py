import re
from datetime import datetime
from enum import Enum
from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash

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
