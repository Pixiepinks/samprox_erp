import re

from marshmallow import Schema, fields
from marshmallow.validate import Range

from models import (
    PayCategory,
    TeamMemberStatus,
    MaintenanceJobStatus,
    ResponsibilityAction,
    ResponsibilityDelegation,
    ResponsibilityTask,
    ResponsibilityRecurrence,
    ResponsibilityTaskStatus,
    ResponsibilityPerformanceUnit,
    User,
)
from responsibility_performance import (
    calculate_metric,
    format_metric,
    format_performance_value,
    parse_performance_value,
    unit_input_type,
)

class UserSchema(Schema):
    id = fields.Int()
    name = fields.Str()
    email = fields.Str()
    role = fields.Str()


# schemas.py
from datetime import datetime, date
from zoneinfo import ZoneInfo

from marshmallow import Schema, fields, validates, validates_schema, ValidationError, pre_load

# --- helpers ---------------------------------------------------------------

VALID_STATUSES = {
    "active": "Active",
    "inactive": "Inactive",
    "on leave": "On Leave",
    "on_leave": "On Leave",
    "on-leave": "On Leave",
}

VALID_PAY_CATEGORIES = {value.lower(): value for value in (
    PayCategory.OFFICE.value,
    PayCategory.FACTORY.value,
    PayCategory.CASUAL.value,
    PayCategory.LOADING.value,
    PayCategory.TRANSPORT.value,
    PayCategory.MAINTENANCE.value,
    PayCategory.OTHER.value,
)}


COLOMBO_ZONE = ZoneInfo("Asia/Colombo")
UTC_ZONE = ZoneInfo("UTC")


def ensure_colombo_datetime(value, *, assume_local: bool = False) -> datetime | None:
    """Return ``value`` converted to the Asia/Colombo timezone."""

    if not isinstance(value, datetime):
        return None

    dt_value = value
    if dt_value.tzinfo is None:
        tz = COLOMBO_ZONE if assume_local else UTC_ZONE
        dt_value = dt_value.replace(tzinfo=tz)

    try:
        return dt_value.astimezone(COLOMBO_ZONE)
    except Exception:  # pragma: no cover - defensive guard
        return None


def format_datetime_as_colombo_iso(value, *, assume_local: bool = False) -> str | None:
    """Return an ISO 8601 string in the Asia/Colombo timezone."""

    converted = ensure_colombo_datetime(value, assume_local=assume_local)
    return converted.isoformat() if converted is not None else None

def _parse_flexible_date(v):
    """Return a date object from either YYYY-MM-DD or MM/DD/YYYY; None if empty."""
    if not v:
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        v = v.strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
    raise ValidationError("Invalid date for joinDate. Please use YYYY-MM-DD.")

def _normalize_status(s):
    if not s:
        return "Active"
    return VALID_STATUSES.get(str(s).strip().lower(), "Active")


def _normalize_pay_category(value):
    if not value:
        return PayCategory.OFFICE.value

    text = str(value).strip()
    if not text:
        return PayCategory.OFFICE.value

    direct = VALID_PAY_CATEGORIES.get(text.lower())
    if direct:
        return direct

    normalized = re.sub(r"[\s_-]+", " ", text).strip().title()
    return VALID_PAY_CATEGORIES.get(normalized.lower(), PayCategory.OFFICE.value)

# --- READ schema (what you send back to the UI) ---------------------------

class TeamMemberSchema(Schema):
    """Serialize DB model -> JSON for the UI."""
    id = fields.Int(dump_only=True)

    # JSON uses camelCase; map to model attrs using data_key / attribute
    reg_number = fields.Str(data_key="regNumber")
    name = fields.Str()
    nickname = fields.Str(allow_none=True)
    epf = fields.Str(allow_none=True)
    position = fields.Str(allow_none=True)
    pay_category = fields.Method("get_pay_category", data_key="payCategory")

    join_date = fields.Date(data_key="joinDate")

    # status is an Enum on the model; we just expose the value
    status = fields.Method("get_status")

    image = fields.Str(attribute="image_url", data_key="image", allow_none=True)

    personal_detail  = fields.Str(attribute="personal_detail",  data_key="personalDetail",  allow_none=True)
    assignments      = fields.Str(allow_none=True)
    training_records = fields.Str(attribute="training_records", data_key="trainingRecords", allow_none=True)
    employment_log   = fields.Str(attribute="employment_log",   data_key="employmentLog",   allow_none=True)
    files            = fields.Str(allow_none=True)
    assets           = fields.Str(allow_none=True)
    bank_account_name   = fields.Str(attribute="bank_account_name",   data_key="bankAccountName",   allow_none=True)
    bank_name           = fields.Str(attribute="bank_name",           data_key="bankName",           allow_none=True)
    branch_name         = fields.Str(attribute="branch_name",         data_key="branchName",         allow_none=True)
    bank_account_number = fields.Str(attribute="bank_account_number", data_key="bankAccountNumber", allow_none=True)

    created_at = fields.DateTime(data_key="createdAt", dump_only=True)
    updated_at = fields.DateTime(data_key="updatedAt", dump_only=True)

    class Meta:
        ordered = True

    # Expose enum value (e.g., "Active")
    def get_status(self, obj):
        value = getattr(obj, "status", None)
        if isinstance(value, str):
            # Handle legacy raw strings that may still be present
            try:
                value = TeamMemberStatus(value)
            except Exception:
                return value

        if isinstance(value, TeamMemberStatus):
            return value.label

        return value

    def get_pay_category(self, obj):
        value = getattr(obj, "pay_category", None)
        if isinstance(value, PayCategory):
            return value.value
        if isinstance(value, str):
            normalized = VALID_PAY_CATEGORIES.get(value.strip().lower())
            return normalized or value
        return value

# --- CREATE/UPDATE schema (what you accept from the UI) -------------------

class TeamMemberCreateSchema(Schema):
    """Validate JSON -> Python for creates/updates from the UI."""
    # Required
    reg_number = fields.Str(required=True, data_key="regNumber")
    name       = fields.Str(required=True)

    # Optional
    nickname = fields.Str(allow_none=True)
    epf      = fields.Str(allow_none=True)
    position = fields.Str(allow_none=True)
    pay_category = fields.Str(allow_none=True, data_key="payCategory")

    # Accept from UI as "joinDate" (string), then we coerce to date in pre_load
    join_date = fields.Date(allow_none=True, data_key="joinDate")

    status = fields.Str(allow_none=True)  # normalized in pre_load

    image = fields.Str(attribute="image_url", data_key="image", allow_none=True)

    personal_detail  = fields.Str(attribute="personal_detail",  data_key="personalDetail",  allow_none=True)
    assignments      = fields.Str(allow_none=True)
    training_records = fields.Str(attribute="training_records", data_key="trainingRecords", allow_none=True)
    employment_log   = fields.Str(attribute="employment_log",   data_key="employmentLog",   allow_none=True)
    files            = fields.Str(allow_none=True)
    assets           = fields.Str(allow_none=True)
    bank_account_name   = fields.Str(attribute="bank_account_name",   data_key="bankAccountName",   allow_none=True)
    bank_name           = fields.Str(attribute="bank_name",           data_key="bankName",           allow_none=True)
    branch_name         = fields.Str(attribute="branch_name",         data_key="branchName",         allow_none=True)
    bank_account_number = fields.Str(attribute="bank_account_number", data_key="bankAccountNumber", allow_none=True)


class TeamMemberBankDetailSchema(Schema):
    member_id = fields.Int(attribute="id", data_key="memberId")
    bank_account_name   = fields.Str(attribute="bank_account_name",   data_key="bankAccountName",   allow_none=True)
    bank_name           = fields.Str(attribute="bank_name",           data_key="bankName",           allow_none=True)
    branch_name         = fields.Str(attribute="branch_name",         data_key="branchName",         allow_none=True)
    bank_account_number = fields.Str(attribute="bank_account_number", data_key="bankAccountNumber", allow_none=True)

    class Meta:
        ordered = True

    @pre_load
    def normalize_inputs(self, in_data, **kwargs):
        """
        - Map/normalize status to DB enum casing.
        - Convert joinDate (string) into ISO 'YYYY-MM-DD' that fields.Date can load.
        """
        data = dict(in_data or {})

        # normalize status
        if "status" in data:
            data["status"] = _normalize_status(data.get("status"))

        if "payCategory" in data:
            data["payCategory"] = _normalize_pay_category(data.get("payCategory"))

        # normalize join date: accept 'YYYY-MM-DD' or 'MM/DD/YYYY'
        jd = data.get("joinDate")
        if jd:
            parsed = _parse_flexible_date(jd)  # raises ValidationError if bad
            data["joinDate"] = parsed.isoformat()

        return data

    @validates("status")
    def validate_status_member(self, value):
        if value not in {"Active", "Inactive", "On Leave"}:
            raise ValidationError("Status must be one of: Active, On Leave, Inactive.")

    @validates("pay_category")
    def validate_pay_category(self, value):
        if value is None:
            return
        if value not in VALID_PAY_CATEGORIES.values():
            raise ValidationError(
                "Pay category must be one of: Office, Factory, Casual, Loading, Transport, Maintenance, Other."
            )



class AttendanceEntrySchema(Schema):
    onTime = fields.Str(allow_none=True)
    offTime = fields.Str(allow_none=True)
    dayStatus = fields.Str(allow_none=True)


class LeaveSummaryBucketSchema(Schema):
    brought_forward = fields.Int(data_key="broughtForward")
    this_month = fields.Int(data_key="thisMonth")
    balance = fields.Int()


class LeaveSummarySchema(Schema):
    work_days = fields.Int(data_key="workDays")
    no_pay_days = fields.Int(data_key="noPayDays")
    annual = fields.Nested(LeaveSummaryBucketSchema)
    medical = fields.Nested(LeaveSummaryBucketSchema)


class AttendanceRecordSchema(Schema):
    member_id = fields.Int(attribute="team_member_id", data_key="memberId")
    month = fields.Str()
    entries = fields.Dict(
        keys=fields.Str(),
        values=fields.Nested(AttendanceEntrySchema),
        allow_none=True,
    )
    leave_summary = fields.Nested(LeaveSummarySchema, data_key="leaveSummary", allow_none=True)
    updated_at = fields.DateTime(data_key="updatedAt")


class WorkCalendarDaySchema(Schema):
    date = fields.Date()
    is_work_day = fields.Bool(attribute="is_work_day", data_key="isWorkDay")
    holiday_name = fields.Str(attribute="holiday_name", allow_none=True, data_key="holidayName")
    updated_at = fields.DateTime(attribute="updated_at", allow_none=True, data_key="updatedAt")


class SalaryRecordSchema(Schema):
    member_id = fields.Int(attribute="team_member_id", data_key="memberId")
    month = fields.Str()
    components = fields.Dict(keys=fields.Str(), values=fields.Raw(), allow_none=True)
    updated_at = fields.DateTime(data_key="updatedAt")


class QuotationSchema(Schema):
    id = fields.Int()
    job_id = fields.Int()
    labor_estimate_hours = fields.Float()
    labor_rate = fields.Float()
    material_estimate_cost = fields.Float()
    notes = fields.Str()
    created_at = fields.DateTime()

class LaborEntrySchema(Schema):
    id = fields.Int()
    job_id = fields.Int()
    user_id = fields.Int()
    date = fields.Date()
    hours = fields.Float()
    rate = fields.Float()
    note = fields.Str()

class MaterialEntrySchema(Schema):
    id = fields.Int()
    job_id = fields.Int()
    item_name = fields.Str()
    qty = fields.Float()
    unit_cost = fields.Float()
    note = fields.Str()

class JobSchema(Schema):
    id = fields.Int()
    code = fields.Str()
    title = fields.Str()
    description = fields.Str()
    status = fields.Str()
    priority = fields.Str()
    location = fields.Str()
    expected_completion_date = fields.Date(allow_none=True)
    completed_date = fields.Date(allow_none=True)
    created_by = fields.Nested(UserSchema)
    assigned_to = fields.Nested(UserSchema, allow_none=True)
    created_at = fields.DateTime()
    updated_at = fields.DateTime()
    progress_pct = fields.Method("get_progress")

    def get_progress(self, obj):
        return obj.progress_pct


class MaintenanceMaterialSchema(Schema):
    id = fields.Int()
    material_name = fields.Str()
    units = fields.Str(allow_none=True)
    cost = fields.Float(allow_none=True)


class MaintenanceOutsourcedServiceSchema(Schema):
    id = fields.Int()
    supplier_id = fields.Int()
    service_date = fields.Date()
    service_description = fields.Str()
    engaged_hours = fields.Float(allow_none=True)
    cost = fields.Float()
    supplier = fields.Nested(
        "ServiceSupplierSchema",
        only=("id", "name", "contact_person"),
        allow_none=True,
    )


class MaintenanceInternalStaffCostSchema(Schema):
    id = fields.Int()
    employee_id = fields.Int()
    service_date = fields.Date()
    work_description = fields.Str()
    engaged_hours = fields.Float(allow_none=True)
    hourly_rate = fields.Float(allow_none=True)
    cost = fields.Float()
    employee = fields.Nested(
        TeamMemberSchema,
        only=("id", "reg_number", "name", "status"),
        allow_none=True,
    )


class MaintenanceJobSchema(Schema):
    id = fields.Int()
    job_code = fields.Str()
    job_date = fields.Date()
    title = fields.Str()
    job_category = fields.Str()
    priority = fields.Str()
    location = fields.Str(allow_none=True)
    description = fields.Str(allow_none=True)
    expected_completion = fields.Date(allow_none=True)
    status = fields.Str()
    prod_email = fields.Str(allow_none=True)
    maint_email = fields.Str(allow_none=True)
    prod_submitted_at = fields.DateTime(allow_none=True)
    maint_submitted_at = fields.DateTime(allow_none=True)
    job_started_date = fields.Date(allow_none=True)
    job_finished_date = fields.Date(allow_none=True)
    total_cost = fields.Float()
    maintenance_notes = fields.Str(allow_none=True)
    created_at = fields.DateTime()
    updated_at = fields.DateTime()
    created_by = fields.Nested(UserSchema)
    assigned_to = fields.Nested(UserSchema, allow_none=True)
    asset_id = fields.Int(allow_none=True)
    part_id = fields.Int(allow_none=True)
    asset = fields.Nested(
        "MachineAssetSchema", only=("id", "code", "name"), allow_none=True
    )
    part = fields.Nested(
        "MachinePartSchema",
        only=("id", "name", "part_number", "asset_id"),
        allow_none=True,
    )
    materials = fields.Nested(MaintenanceMaterialSchema, many=True)
    outsourced_services = fields.Nested(MaintenanceOutsourcedServiceSchema, many=True)
    internal_staff_costs = fields.Nested(MaintenanceInternalStaffCostSchema, many=True)

    status_label = fields.Method("get_status_label")

    def get_status_label(self, obj):
        status = getattr(obj, "status", None)
        if isinstance(status, MaintenanceJobStatus):
            return status.value
        if isinstance(status, str):
            try:
                return MaintenanceJobStatus(status).value
            except ValueError:
                return status
        return status


class MachinePartReplacementSchema(Schema):
    id = fields.Int()
    part_id = fields.Int()
    replaced_on = fields.Date()
    replaced_by = fields.Str(allow_none=True)
    reason = fields.Str(allow_none=True)
    notes = fields.Str(allow_none=True)


class SupplierSchema(Schema):
    id = fields.UUID()
    name = fields.Str(required=True)
    primary_phone = fields.Str(attribute="primary_phone", allow_none=True, data_key="primaryPhone")
    secondary_phone = fields.Str(attribute="secondary_phone", allow_none=True, data_key="secondaryPhone")
    category = fields.Str(allow_none=True)
    vehicle_no_1 = fields.Str(attribute="vehicle_no_1", allow_none=True, data_key="vehicleNo1")
    vehicle_no_2 = fields.Str(attribute="vehicle_no_2", allow_none=True, data_key="vehicleNo2")
    vehicle_no_3 = fields.Str(attribute="vehicle_no_3", allow_none=True, data_key="vehicleNo3")
    supplier_id_no = fields.Str(attribute="supplier_id_no", allow_none=True, data_key="supplierIdNo")
    supplier_reg_no = fields.Str(attribute="supplier_reg_no", data_key="supplierRegNo")
    credit_period = fields.Str(attribute="credit_period", allow_none=True, data_key="creditPeriod")
    phone = fields.Method("get_primary_phone", dump_only=True)
    email = fields.Str(allow_none=True)
    address = fields.Str(allow_none=True)
    tax_id = fields.Str(allow_none=True, data_key="taxId")

    def get_primary_phone(self, obj):
        try:
            return obj.primary_phone
        except AttributeError:
            return None


class MaterialItemSchema(Schema):
    id = fields.UUID()
    name = fields.Str(required=True)
    is_active = fields.Bool()


class MRNLineSchema(Schema):
    id = fields.UUID()
    mrn_id = fields.UUID()
    item_id = fields.UUID(required=True)
    first_weight_kg = fields.Decimal(as_string=True)
    second_weight_kg = fields.Decimal(as_string=True)
    qty_ton = fields.Decimal(as_string=True)
    unit_price = fields.Decimal(as_string=True)
    wet_factor = fields.Decimal(as_string=True)
    approved_unit_price = fields.Decimal(as_string=True)
    amount = fields.Decimal(as_string=True)
    created_at = fields.DateTime()
    updated_at = fields.DateTime()

    item = fields.Nested(MaterialItemSchema, allow_none=True)


class MRNSchema(Schema):
    id = fields.UUID()
    mrn_no = fields.Str(required=True)
    date = fields.Date(required=True)
    supplier_id = fields.UUID(allow_none=True)
    sourcing_type = fields.Str()
    vehicle_no = fields.Str(allow_none=True)
    qty_ton = fields.Decimal(as_string=True)
    amount = fields.Decimal(as_string=True)
    weighing_slip_no = fields.Str()
    weigh_in_time = fields.DateTime()
    weigh_out_time = fields.DateTime()
    security_officer_name = fields.Str()
    authorized_person_name = fields.Str()
    driver_id = fields.Int(allow_none=True)
    helper1_id = fields.Int(allow_none=True)
    helper2_id = fields.Int(allow_none=True)
    created_by = fields.Int(allow_none=True)
    created_at = fields.DateTime()
    updated_at = fields.DateTime()

    supplier = fields.Nested(SupplierSchema, allow_none=True)
    driver = fields.Nested(TeamMemberSchema, allow_none=True)
    helper1 = fields.Nested(TeamMemberSchema, allow_none=True)
    helper2 = fields.Nested(TeamMemberSchema, allow_none=True)
    items = fields.Nested(MRNLineSchema, many=True)


class MachinePartSchema(Schema):
    id = fields.Int()
    asset_id = fields.Int()
    name = fields.Str()
    part_number = fields.Str(allow_none=True)
    description = fields.Str(allow_none=True)
    expected_life_hours = fields.Int(allow_none=True)
    notes = fields.Str(allow_none=True)
    replacement_history = fields.Nested(MachinePartReplacementSchema, many=True)


class MachineAssetSchema(Schema):
    id = fields.Int()
    code = fields.Str()
    name = fields.Str()
    category = fields.Str(allow_none=True)
    location = fields.Str(allow_none=True)
    manufacturer = fields.Str(allow_none=True)
    model_number = fields.Str(allow_none=True)
    serial_number = fields.Str(allow_none=True)
    installed_on = fields.Date(allow_none=True)
    status = fields.Str(allow_none=True)
    notes = fields.Str(allow_none=True)
    part_count = fields.Method("get_part_count")

    def get_part_count(self, obj):
        try:
            return len(obj.parts)
        except TypeError:
            return 0


class MachineIdleEventSchema(Schema):
    id = fields.Int()
    asset_id = fields.Int()
    started_at = fields.Method("get_started_at")
    ended_at = fields.Method("get_ended_at", allow_none=True)
    reason = fields.Str(allow_none=True)
    secondary_reason = fields.Str(allow_none=True)
    notes = fields.Str(allow_none=True)
    duration_minutes = fields.Int(allow_none=True)
    asset = fields.Nested(MachineAssetSchema(only=("id", "name", "code")))

    def get_started_at(self, obj):
        return format_datetime_as_colombo_iso(
            getattr(obj, "started_at", None), assume_local=True
        )

    def get_ended_at(self, obj):
        return format_datetime_as_colombo_iso(
            getattr(obj, "ended_at", None), assume_local=True
        )


class ServiceSupplierSchema(Schema):
    id = fields.Int()
    name = fields.Str()
    contact_person = fields.Str(allow_none=True)
    phone = fields.Str(allow_none=True)
    email = fields.Str(allow_none=True)
    services_offered = fields.Str(allow_none=True)
    preferred_assets = fields.Str(allow_none=True)
    notes = fields.Str(allow_none=True)
    created_at = fields.DateTime()


class DailyProductionEntrySchema(Schema):
    id = fields.Int()
    date = fields.Date()
    hour_no = fields.Int()
    quantity_tons = fields.Float()
    asset_id = fields.Int()
    machine_code = fields.Method("get_machine_code")
    machine_name = fields.Method("get_machine_name")
    updated_at = fields.Method("get_updated_at", allow_none=True)

    def get_machine_code(self, obj):
        try:
            return obj.asset.code
        except AttributeError:
            return None

    def get_machine_name(self, obj):
        try:
            return obj.asset.name
        except AttributeError:
            return None

    def get_updated_at(self, obj):
        value = getattr(obj, "updated_at", None)
        if value is None:
            value = getattr(obj, "created_at", None)
        return format_datetime_as_colombo_iso(value)


class BriquetteMixEntrySchema(Schema):
    id = fields.Int()
    date = fields.Date()
    dry_factor = fields.Decimal(as_string=True, allow_none=True)
    sawdust_qty_ton = fields.Decimal(as_string=True)
    wood_shaving_qty_ton = fields.Decimal(as_string=True)
    wood_powder_qty_ton = fields.Decimal(as_string=True)
    peanut_husk_qty_ton = fields.Decimal(as_string=True)
    fire_cut_qty_ton = fields.Decimal(as_string=True)
    total_material_cost = fields.Decimal(as_string=True)
    unit_cost_per_kg = fields.Decimal(as_string=True)
    total_output_kg = fields.Decimal(as_string=True)
    cost_breakdown = fields.Raw()
    created_at = fields.DateTime()
    updated_at = fields.DateTime()


class ProductionForecastEntrySchema(Schema):
    id = fields.Int()
    date = fields.Date()
    forecast_tons = fields.Float()
    forecast_hours = fields.Float()
    average_hourly_production = fields.Float()
    asset_id = fields.Int()
    machine_code = fields.Method("get_machine_code")
    machine_name = fields.Method("get_machine_name")
    created_at = fields.Method("get_created_at", allow_none=True)
    updated_at = fields.Method("get_updated_at", allow_none=True)

    def get_machine_code(self, obj):
        try:
            return obj.asset.code
        except AttributeError:
            return None

    def get_machine_name(self, obj):
        try:
            return obj.asset.name
        except AttributeError:
            return None

    def get_created_at(self, obj):
        return format_datetime_as_colombo_iso(getattr(obj, "created_at", None))

    def get_updated_at(self, obj):
        return format_datetime_as_colombo_iso(getattr(obj, "updated_at", None))


RESPONSIBILITY_WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def _ordinal_suffix(value: int) -> str:
    if 10 <= value % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")


def describe_responsibility_recurrence(task: ResponsibilityTask) -> str | None:
    if task is None:
        return None

    recurrence = getattr(task, "recurrence", None)
    if not isinstance(recurrence, ResponsibilityRecurrence):
        try:
            recurrence = ResponsibilityRecurrence(recurrence)
        except Exception:
            return None

    scheduled_for = getattr(task, "scheduled_for", None)

    if recurrence == ResponsibilityRecurrence.DOES_NOT_REPEAT:
        return "Does not repeat"
    if recurrence == ResponsibilityRecurrence.MONDAY_TO_FRIDAY:
        return "Monday to Friday"
    if recurrence == ResponsibilityRecurrence.DAILY:
        return "Daily"
    if recurrence == ResponsibilityRecurrence.WEEKLY and isinstance(scheduled_for, date):
        return f"Weekly on {RESPONSIBILITY_WEEKDAY_NAMES[scheduled_for.weekday()]}"
    if recurrence == ResponsibilityRecurrence.MONTHLY and isinstance(scheduled_for, date):
        day = scheduled_for.day
        return f"Monthly on the {day}{_ordinal_suffix(day)}"
    if recurrence == ResponsibilityRecurrence.ANNUALLY and isinstance(scheduled_for, date):
        return scheduled_for.strftime("Annually on %B %d")
    if recurrence == ResponsibilityRecurrence.CUSTOM:
        weekdays = task.custom_weekday_list
        if weekdays:
            names = [RESPONSIBILITY_WEEKDAY_NAMES[index] for index in weekdays]
            return f"Custom ({', '.join(names)})"
        return "Custom"
    return None


class ResponsibilityDelegationSchema(Schema):
    id = fields.Int(dump_only=True)
    delegate = fields.Nested(UserSchema, dump_only=True, allow_none=True)
    delegate_id = fields.Method("get_delegate_id", data_key="delegateId")
    delegate_name = fields.Method("get_delegate_name", data_key="delegateName")
    allocated_value = fields.Decimal(
        allow_none=True,
        as_string=True,
        data_key="allocatedValue",
    )

    def get_delegate_id(self, obj):
        member_id = getattr(obj, "delegate_member_id", None)
        if member_id is not None:
            return member_id
        return getattr(obj, "delegate_id", None)

    def get_delegate_name(self, obj):
        member = getattr(obj, "delegate_member", None)
        name = getattr(member, "name", None)
        if isinstance(name, str):
            stripped = name.strip()
            if stripped:
                return stripped
        delegate = getattr(obj, "delegate", None)
        user_name = getattr(delegate, "name", None)
        if isinstance(user_name, str):
            stripped_user = user_name.strip()
            if stripped_user:
                return stripped_user
        email = getattr(delegate, "email", None)
        if isinstance(email, str):
            stripped_email = email.strip()
            if stripped_email:
                return stripped_email
        return None


class ResponsibilityTaskSchema(Schema):
    id = fields.Int(dump_only=True)
    number = fields.Str()
    title = fields.Str(required=True)
    description = fields.Str(allow_none=True)
    detail = fields.Str(allow_none=True)
    scheduled_for = fields.Date(data_key="scheduledFor")
    recurrence = fields.Method("get_recurrence")
    recurrence_label = fields.Method("get_recurrence_label", data_key="recurrenceLabel")
    custom_weekdays = fields.Method("get_custom_weekdays", data_key="customWeekdays")
    status = fields.Method("get_status")
    action = fields.Method("get_action")
    action_notes = fields.Str(allow_none=True, data_key="actionNotes")
    progress = fields.Int(data_key="progress")
    recipient_email = fields.Str(data_key="recipientEmail")
    cc_email = fields.Str(allow_none=True, data_key="ccEmail")
    performance_unit = fields.Method("get_performance_unit", data_key="performanceUnit")
    performance_input_type = fields.Method(
        "get_performance_input_type",
        data_key="performanceInputType",
    )
    performance_responsible = fields.Method(
        "get_performance_responsible",
        data_key="performanceResponsible",
    )
    performance_actual = fields.Method(
        "get_performance_actual",
        data_key="performanceActual",
    )
    performance_metric = fields.Method(
        "get_performance_metric",
        data_key="performanceMetric",
    )
    assigner = fields.Nested(UserSchema, dump_only=True)
    assignee = fields.Nested(UserSchema, dump_only=True, allow_none=True)
    delegated_to = fields.Nested(UserSchema, dump_only=True, allow_none=True, data_key="delegatedTo")
    assignee_id = fields.Method("get_assignee_id", data_key="assigneeId")
    assignee_name = fields.Method("get_assignee_name", data_key="assigneeName")
    assignee_email = fields.Method("get_assignee_email", data_key="assigneeEmail")
    delegated_to_id = fields.Method("get_delegated_to_id", data_key="delegatedToId")
    delegated_to_name = fields.Method("get_delegated_to_name", data_key="delegatedToName")
    delegations = fields.List(
        fields.Nested(ResponsibilityDelegationSchema),
        dump_only=True,
        data_key="delegations",
        dump_default=[],
    )
    created_at = fields.Method("get_created_at", data_key="createdAt")
    updated_at = fields.Method("get_updated_at", data_key="updatedAt")

    class Meta:
        ordered = True

    def get_custom_weekdays(self, obj):
        if not isinstance(obj, ResponsibilityTask):
            return []
        return obj.custom_weekday_list

    def get_recurrence(self, obj):
        value = getattr(obj, "recurrence", None)
        if isinstance(value, ResponsibilityRecurrence):
            return value.value
        if isinstance(value, str):
            return value
        return None

    def get_recurrence_label(self, obj):
        return describe_responsibility_recurrence(obj)

    def get_status(self, obj):
        value = getattr(obj, "status", None)
        if isinstance(value, ResponsibilityTaskStatus):
            return value.value
        if isinstance(value, str):
            return value
        return None

    def get_action(self, obj):
        value = getattr(obj, "action", None)
        if isinstance(value, ResponsibilityAction):
            return value.value
        if isinstance(value, str):
            return value
        return None

    def get_created_at(self, obj):
        return format_datetime_as_colombo_iso(getattr(obj, "created_at", None), assume_local=True)

    def get_updated_at(self, obj):
        return format_datetime_as_colombo_iso(getattr(obj, "updated_at", None), assume_local=True)

    def _resolve_unit(self, obj) -> ResponsibilityPerformanceUnit:
        value = getattr(obj, "perf_uom", None)
        if isinstance(value, ResponsibilityPerformanceUnit):
            return value
        if isinstance(value, str):
            try:
                return ResponsibilityPerformanceUnit(value)
            except ValueError:
                return ResponsibilityPerformanceUnit.PERCENTAGE_PCT
        return ResponsibilityPerformanceUnit.PERCENTAGE_PCT

    def get_performance_unit(self, obj):
        unit = getattr(obj, "perf_uom", None)
        if isinstance(unit, ResponsibilityPerformanceUnit):
            return unit.value
        if isinstance(unit, str):
            return unit
        return ResponsibilityPerformanceUnit.PERCENTAGE_PCT.value

    def get_performance_input_type(self, obj):
        unit = self._resolve_unit(obj)
        return unit_input_type(unit)

    def _format_performance_value(self, obj, attribute):
        unit = self._resolve_unit(obj)
        value = getattr(obj, attribute, None)
        return format_performance_value(unit, value)

    def get_performance_responsible(self, obj):
        return self._format_performance_value(obj, "perf_responsible_value")

    def get_performance_actual(self, obj):
        return self._format_performance_value(obj, "perf_actual_value")

    def get_performance_metric(self, obj):
        unit = self._resolve_unit(obj)
        metric = getattr(obj, "perf_metric_value", None)
        return format_metric(unit, metric)

    def get_assignee_id(self, obj):
        member_id = getattr(obj, "assignee_member_id", None)
        if member_id is not None:
            return member_id
        return getattr(obj, "assignee_id", None)

    def get_assignee_name(self, obj):
        member = getattr(obj, "assignee_member", None)
        name = getattr(member, "name", None)
        if isinstance(name, str):
            stripped = name.strip()
            if stripped:
                return stripped
        assignee = getattr(obj, "assignee", None)
        user_name = getattr(assignee, "name", None)
        if isinstance(user_name, str):
            stripped_user = user_name.strip()
            if stripped_user:
                return stripped_user
        email = getattr(assignee, "email", None)
        if isinstance(email, str):
            stripped_email = email.strip()
            if stripped_email:
                return stripped_email
        return None

    def get_assignee_email(self, obj):
        assignee = getattr(obj, "assignee", None)
        email = getattr(assignee, "email", None)
        if isinstance(email, str):
            stripped = email.strip()
            if stripped:
                return stripped
        return None

    def get_delegated_to_id(self, obj):
        member_id = getattr(obj, "delegated_to_member_id", None)
        if member_id is not None:
            return member_id
        delegations = getattr(obj, "delegations", None) or []
        for delegation in delegations:
            delegate_member_id = getattr(delegation, "delegate_member_id", None)
            if delegate_member_id is not None:
                return delegate_member_id
        if delegations:
            first = delegations[0]
            delegate_id = getattr(first, "delegate_id", None)
            if delegate_id is not None:
                return delegate_id
        return getattr(obj, "delegated_to_id", None)

    def get_delegated_to_name(self, obj):
        member = getattr(obj, "delegated_to_member", None)
        name = getattr(member, "name", None)
        if isinstance(name, str):
            stripped = name.strip()
            if stripped:
                return stripped
        delegated = getattr(obj, "delegated_to", None)
        delegations = getattr(obj, "delegations", None) or []
        if delegations:
            first = delegations[0]
            delegate_member = getattr(first, "delegate_member", None)
            member_name = getattr(delegate_member, "name", None)
            if isinstance(member_name, str):
                stripped_member = member_name.strip()
                if stripped_member:
                    return stripped_member
        for delegation in delegations[1:]:
            member = getattr(delegation, "delegate_member", None)
            member_name = getattr(member, "name", None)
            if isinstance(member_name, str):
                stripped_member = member_name.strip()
                if stripped_member:
                    return stripped_member
        user_name = getattr(delegated, "name", None)
        if isinstance(user_name, str):
            stripped_user = user_name.strip()
            if stripped_user:
                return stripped_user
        if delegations:
            first = delegations[0]
            delegate = getattr(first, "delegate", None)
            first_user_name = getattr(delegate, "name", None)
            if isinstance(first_user_name, str):
                stripped_first_user = first_user_name.strip()
                if stripped_first_user:
                    return stripped_first_user
            email = getattr(delegate, "email", None)
            if isinstance(email, str):
                stripped_email = email.strip()
                if stripped_email:
                    return stripped_email
        for delegation in delegations[1:]:
            delegate = getattr(delegation, "delegate", None)
            user_name = getattr(delegate, "name", None)
            if isinstance(user_name, str):
                stripped_user = user_name.strip()
                if stripped_user:
                    return stripped_user
        for delegation in delegations[1:]:
            delegate = getattr(delegation, "delegate", None)
            email = getattr(delegate, "email", None)
            if isinstance(email, str):
                stripped_email = email.strip()
                if stripped_email:
                    return stripped_email
        email = getattr(delegated, "email", None)
        if isinstance(email, str):
            stripped_email = email.strip()
            if stripped_email:
                return stripped_email
        return None


class ResponsibilityDelegationCreateSchema(Schema):
    delegate_id = fields.Int(required=True, data_key="delegateId")
    allocated_value = fields.Decimal(
        allow_none=True,
        as_string=True,
        data_key="allocatedValue",
        places=4,
    )
    delegate_type = fields.Str(load_default=None, data_key="delegateType")


class ResponsibilityTaskCreateSchema(Schema):
    title = fields.Str(required=True)
    description = fields.Str(allow_none=True)
    detail = fields.Str(allow_none=True)
    scheduled_for = fields.Date(required=True, data_key="scheduledFor")
    recurrence = fields.Str(required=True, data_key="recurrence")
    custom_weekdays = fields.List(fields.Int(), allow_none=True, data_key="customWeekdays")
    assignee_id = fields.Int(required=True, allow_none=False, data_key="assigneeId")
    assignee_type = fields.Str(load_default=None, data_key="assigneeType")
    delegated_to_id = fields.Int(allow_none=True, data_key="delegatedToId")
    delegated_to_type = fields.Str(load_default=None, data_key="delegatedToType")
    delegations = fields.List(
        fields.Nested(ResponsibilityDelegationCreateSchema),
        allow_none=True,
        data_key="delegations",
    )
    recipient_email = fields.Email(required=True, data_key="recipientEmail")
    cc_email = fields.Email(allow_none=True, load_default=None, data_key="ccEmail")
    status = fields.Str(load_default=ResponsibilityTaskStatus.PLANNED.value)
    action = fields.Str(required=True)
    action_notes = fields.Str(allow_none=True, data_key="actionNotes")
    progress = fields.Int(allow_none=True, validate=Range(min=0, max=100))
    performance_unit = fields.Str(required=True, data_key="performanceUnit")
    performance_responsible = fields.Decimal(
        required=True,
        data_key="performanceResponsible",
        allow_none=False,
        as_string=True,
        places=4,
    )
    performance_actual = fields.Decimal(
        allow_none=True,
        data_key="performanceActual",
        as_string=True,
        places=4,
    )

    class Meta:
        ordered = True

    @pre_load
    def normalize_payload(self, data, **_kwargs):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)

        recurrence = normalized.get("recurrence")
        if isinstance(recurrence, str):
            normalized["recurrence"] = recurrence.strip().lower().replace(" ", "_")

        custom_weekdays = normalized.get("customWeekdays")
        if isinstance(custom_weekdays, str):
            normalized["customWeekdays"] = [
                int(part)
                for part in custom_weekdays.split(",")
                if part.strip().isdigit()
            ]
        elif isinstance(custom_weekdays, (set, tuple)):
            normalized["customWeekdays"] = list(custom_weekdays)

        cc_email = normalized.get("ccEmail")
        if isinstance(cc_email, str):
            stripped_cc = cc_email.strip()
            normalized["ccEmail"] = stripped_cc or None

        status = normalized.get("status")
        if isinstance(status, str):
            normalized["status"] = status.strip().lower()

        action = normalized.get("action")
        if isinstance(action, str):
            normalized["action"] = action.strip().lower()

        assignee_type = normalized.get("assigneeType")
        if isinstance(assignee_type, str):
            normalized["assigneeType"] = assignee_type.strip().lower().replace(" ", "_")

        delegated_type = normalized.get("delegatedToType")
        if isinstance(delegated_type, str):
            normalized["delegatedToType"] = delegated_type.strip().lower().replace(" ", "_")

        delegations_payload = normalized.get("delegations")
        if isinstance(delegations_payload, list):
            for entry in delegations_payload:
                if not isinstance(entry, dict):
                    continue
                delegate_type = entry.get("delegateType")
                if isinstance(delegate_type, str):
                    entry["delegateType"] = delegate_type.strip().lower().replace(" ", "_")

        progress = normalized.get("progress")
        if isinstance(progress, str):
            stripped = progress.strip()
            if not stripped:
                normalized["progress"] = None
            else:
                try:
                    normalized["progress"] = int(round(float(stripped)))
                except ValueError:
                    normalized["progress"] = stripped
        elif isinstance(progress, float):
            normalized["progress"] = int(round(progress))

        performance_unit = normalized.get("performanceUnit")
        if isinstance(performance_unit, str):
            normalized_unit = performance_unit.strip().lower().replace(" ", "_")
            normalized["performanceUnit"] = normalized_unit
        else:
            normalized_unit = performance_unit

        unit_enum = None
        if isinstance(normalized_unit, ResponsibilityPerformanceUnit):
            unit_enum = normalized_unit
        elif isinstance(normalized_unit, str):
            try:
                unit_enum = ResponsibilityPerformanceUnit(normalized_unit)
            except ValueError:
                unit_enum = None

        if unit_enum is not None:
            responsible_value = normalized.get("performanceResponsible")
            actual_value = normalized.get("performanceActual")

            try:
                normalized["performanceResponsible"] = parse_performance_value(
                    unit_enum,
                    "" if responsible_value is None else str(responsible_value),
                )
            except ValueError as error:
                raise ValidationError(str(error), field_name="performanceResponsible") from error

            if actual_value is None:
                normalized["performanceActual"] = None
            else:
                try:
                    normalized["performanceActual"] = parse_performance_value(
                        unit_enum,
                        str(actual_value),
                    )
                except ValueError as error:
                    raise ValidationError(str(error), field_name="performanceActual") from error

        return normalized

    @validates("recurrence")
    def validate_recurrence(self, value):
        try:
            ResponsibilityRecurrence(value)
        except ValueError:
            raise ValidationError("Invalid recurrence option.")

    @validates("status")
    def validate_status(self, value):
        if value is None:
            return
        try:
            ResponsibilityTaskStatus(value)
        except ValueError:
            raise ValidationError("Invalid task status.")

    @validates("action")
    def validate_action(self, value):
        try:
            ResponsibilityAction(value)
        except ValueError as error:
            raise ValidationError("Invalid 5D action option.") from error

    @validates("performance_unit")
    def validate_performance_unit(self, value):
        try:
            ResponsibilityPerformanceUnit(value)
        except ValueError as error:
            raise ValidationError("Invalid unit of measure option.") from error

    @validates("custom_weekdays")
    def validate_custom_weekdays(self, value):
        if value is None:
            return
        for index in value:
            try:
                number = int(index)
            except (TypeError, ValueError) as error:
                raise ValidationError("Custom weekdays must be integers between 0 and 6.") from error
            if number < 0 or number > 6:
                raise ValidationError("Custom weekdays must be between 0 (Monday) and 6 (Sunday).")

    @validates_schema
    def validate_custom_combination(self, data, **_kwargs):
        recurrence = data.get("recurrence")
        custom_weekdays = data.get("custom_weekdays") or []
        if recurrence == ResponsibilityRecurrence.CUSTOM.value and not custom_weekdays:
            raise ValidationError(
                "Select at least one weekday for custom recurrence.",
                field_name="customWeekdays",
            )
        if recurrence != ResponsibilityRecurrence.CUSTOM.value and custom_weekdays:
            raise ValidationError(
                "Custom weekdays can only be provided when recurrence is set to custom.",
                field_name="customWeekdays",
            )

        action = data.get("action")
        delegated_to = data.get("delegated_to_id")
        delegations = data.get("delegations") or []
        if action == ResponsibilityAction.DELEGATED.value:
            if delegations:
                missing = [
                    index
                    for index, entry in enumerate(delegations)
                    if not isinstance(entry, dict)
                    or entry.get("delegate_id") in {None, ""}
                ]
                if missing:
                    raise ValidationError("Select a delegated team member.", "delegations")
            elif not delegated_to:
                raise ValidationError("Select a delegated team member.", "delegations")

        if action in {
            ResponsibilityAction.DISCUSSED.value,
            ResponsibilityAction.DEFERRED.value,
            ResponsibilityAction.DELETED.value,
        } and not data.get("action_notes"):
            raise ValidationError(
                "Provide discussion points or reasons for this action.",
                "actionNotes",
            )

        if data.get("performance_responsible") is None:
            raise ValidationError(
                "Enter a responsible target value.",
                field_name="performanceResponsible",
            )
