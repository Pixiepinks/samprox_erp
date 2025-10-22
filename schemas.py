from marshmallow import Schema, fields

class UserSchema(Schema):
    id = fields.Int()
    name = fields.Str()
    email = fields.Str()
    role = fields.Str()


# schemas.py
from datetime import datetime, date
from marshmallow import Schema, fields, ValidationError, pre_load

# --- helpers ---------------------------------------------------------------

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

    join_date = fields.Date(data_key="joinDate")

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

    # Accept from UI as "joinDate" (string), then we coerce to date in pre_load
    join_date = fields.Date(allow_none=True, data_key="joinDate")

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
        Normalize the join date into ISO 'YYYY-MM-DD' so ``fields.Date`` can load it.
        """
        data = dict(in_data or {})

        # normalize join date: accept 'YYYY-MM-DD' or 'MM/DD/YYYY'
        jd = data.get("joinDate")
        if jd:
            parsed = _parse_flexible_date(jd)  # raises ValidationError if bad
            data["joinDate"] = parsed.isoformat()

        return data



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
