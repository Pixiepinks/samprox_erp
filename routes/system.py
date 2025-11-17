from datetime import date, datetime, time, timedelta

from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy import func, or_

from extensions import db
from models import (
    BriquetteMixEntry,
    DailyProductionEntry,
    MachineIdleEvent,
    MRNHeader,
    MRNLine,
    SalesActualEntry,
    TeamAttendanceRecord,
)

bp = Blueprint("system", __name__, url_prefix="/api/system")


@bp.get("/status")
@jwt_required()
def get_system_status():
    today = date.today()
    yesterday = today - timedelta(days=1)
    deadline = time(10, 0)
    now = datetime.now()
    past_deadline = now.time() >= deadline

    def has_records(query):
        try:
            return query.limit(1).first() is not None
        except Exception:
            db.session.rollback()
            return False

    production_ok = has_records(DailyProductionEntry.query.filter_by(date=yesterday))
    sales_ok = has_records(SalesActualEntry.query.filter_by(date=yesterday))
    mrn_ok = has_records(MRNLine.query.join(MRNHeader).filter(MRNHeader.date == yesterday))
    mix_ok = has_records(BriquetteMixEntry.query.filter_by(date=yesterday))
    idle_ok = has_records(
        MachineIdleEvent.query.filter(
            or_(
                func.date(MachineIdleEvent.started_at) == yesterday,
                func.date(MachineIdleEvent.ended_at) == yesterday,
            )
        )
    )

    attendance_month = f"{yesterday.year}-{yesterday.month:02d}"
    attendance_day_key = yesterday.isoformat()
    attendance_records = TeamAttendanceRecord.query.filter_by(month=attendance_month).all()
    attendance_ok = any(
        isinstance(record.entries, dict) and attendance_day_key in record.entries
        for record in attendance_records
    )

    checks = {
        "daily_production_entry": production_ok,
        "sales_actual_entry": sales_ok,
        "mrn_lines": mrn_ok,
        "briquette_mix_entries": mix_ok,
        "machine_idle_event": idle_ok,
        "team_attendance_record": attendance_ok,
    }

    any_missing = any(not status for status in checks.values())
    system_status = "OUT OF DATE" if past_deadline and any_missing else "UPDATED"

    return jsonify(
        {
            "system_status": system_status,
            **{key: ("OK" if value else "Missing") for key, value in checks.items()},
            "deadline": "10:00 AM",
            "last_checked": now.isoformat(),
        }
    )
