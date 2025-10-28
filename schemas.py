import re

from marshmallow import Schema, fields

from models import PayCategory, TeamMemberStatus

class UserSchema(Schema):
    id = fields.Int()
    name = fields.Str()
    email = fields.Str()
    role = fields.Str()


# schemas.py
from datetime import datetime, date
from marshmallow import Schema, fields, validates, ValidationError, pre_load

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
    PayCategory.OTHER.value,
)}

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
            raise ValidationError("Pay category must be one of: Office, Factory, Casual, Other.")



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


class MRNSchema(Schema):
    id = fields.UUID()
    mrn_no = fields.Str(required=True)
    date = fields.Date(required=True)
    supplier_id = fields.UUID(allow_none=True)
    vehicle_no = fields.Str(allow_none=True)
    item_id = fields.UUID(required=True)
    qty_ton = fields.Decimal(as_string=True)
    unit_price = fields.Decimal(as_string=True)
    wet_factor = fields.Decimal(as_string=True)
    approved_unit_price = fields.Decimal(as_string=True)
    amount = fields.Decimal(as_string=True)
    weighing_slip_no = fields.Str()
    weigh_in_weight_kg = fields.Decimal(as_string=True)
    weigh_out_weight_kg = fields.Decimal(as_string=True)
    weigh_in_time = fields.DateTime()
    weigh_out_time = fields.DateTime()
    security_officer_name = fields.Str()
    authorized_person_name = fields.Str()
    created_by = fields.Int(allow_none=True)
    created_at = fields.DateTime()
    updated_at = fields.DateTime()

    supplier = fields.Nested(SupplierSchema, allow_none=True)
    item = fields.Nested(MaterialItemSchema)


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
    started_at = fields.DateTime()
    ended_at = fields.DateTime(allow_none=True)
    reason = fields.Str(allow_none=True)
    secondary_reason = fields.Str(allow_none=True)
    notes = fields.Str(allow_none=True)
    duration_minutes = fields.Int(allow_none=True)
    asset = fields.Nested(MachineAssetSchema(only=("id", "name", "code")))


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
    updated_at = fields.DateTime(allow_none=True)

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


class ProductionForecastEntrySchema(Schema):
    id = fields.Int()
    date = fields.Date()
    forecast_tons = fields.Float()
    forecast_hours = fields.Float()
    average_hourly_production = fields.Float()
    asset_id = fields.Int()
    machine_code = fields.Method("get_machine_code")
    machine_name = fields.Method("get_machine_name")
    created_at = fields.DateTime(allow_none=True)
    updated_at = fields.DateTime(allow_none=True)

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
