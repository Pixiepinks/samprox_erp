from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

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
    now = datetime.now(tz=ZoneInfo("Asia/Colombo"))
    today = now.date()
    yesterday = today - timedelta(days=1)
    deadline = time(10, 0, tzinfo=ZoneInfo("Asia/Colombo"))
    past_deadline = now.timetz() >= deadline

    def count_records(query):
        try:
            return query.count()
        except Exception:
            db.session.rollback()
            return 0

    def evaluate_status(yesterday_count: int, today_count: int):
        if yesterday_count > 0:
            return {"status": "OK", "is_missing": False}

        if today_count > 0:
            return {"status": "OK (no activity yesterday)", "is_missing": False}

        if past_deadline:
            return {"status": "Missing", "is_missing": True}

        return {"status": "OK", "is_missing": False}

    production_yesterday = count_records(DailyProductionEntry.query.filter_by(date=yesterday))
    production_today = count_records(DailyProductionEntry.query.filter_by(date=today))
    production_status = evaluate_status(production_yesterday, production_today)

    sales_yesterday = count_records(SalesActualEntry.query.filter_by(date=yesterday))
    sales_today = count_records(SalesActualEntry.query.filter_by(date=today))
    sales_status = evaluate_status(sales_yesterday, sales_today)

    mrn_yesterday = count_records(MRNLine.query.join(MRNHeader).filter(MRNHeader.date == yesterday))
    mrn_today = count_records(MRNLine.query.join(MRNHeader).filter(MRNHeader.date == today))
    mrn_status = evaluate_status(mrn_yesterday, mrn_today)

    mix_yesterday = count_records(BriquetteMixEntry.query.filter_by(date=yesterday))
    mix_today = count_records(BriquetteMixEntry.query.filter_by(date=today))
    mix_status = evaluate_status(mix_yesterday, mix_today)

    idle_yesterday = count_records(
        MachineIdleEvent.query.filter(
            or_(
                func.date(MachineIdleEvent.started_at) == yesterday,
                func.date(MachineIdleEvent.ended_at) == yesterday,
            )
        )
    )
    idle_today = count_records(
        MachineIdleEvent.query.filter(
            or_(
                func.date(MachineIdleEvent.started_at) == today,
                func.date(MachineIdleEvent.ended_at) == today,
            )
        )
    )
    idle_status = evaluate_status(idle_yesterday, idle_today)

    def attendance_count_for_date(target_date: date) -> int:
        month_key = f"{target_date.year}-{target_date.month:02d}"
        day_key = target_date.isoformat()
        records = TeamAttendanceRecord.query.filter_by(month=month_key).all()
        return sum(
            1
            for record in records
            if isinstance(record.entries, dict) and day_key in record.entries
        )

    attendance_yesterday = attendance_count_for_date(yesterday)
    attendance_today = attendance_count_for_date(today)
    attendance_status = evaluate_status(attendance_yesterday, attendance_today)

    checks = {
        "daily_production_entry": production_status,
        "sales_actual_entry": sales_status,
        "mrn_lines": mrn_status,
        "briquette_mix_entries": mix_status,
        "machine_idle_event": idle_status,
        "team_attendance_record": attendance_status,
    }

    any_missing = any(value["is_missing"] for value in checks.values())
    system_status = "OUT OF DATE" if any_missing else "UPDATED"

    return jsonify(
        {
            "system_status": system_status,
            **{key: value["status"] for key, value in checks.items()},
            "deadline": "10:00 AM",
            "last_checked": now.isoformat(),
        }
    )
