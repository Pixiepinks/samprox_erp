"""Material Receipt Note routes with WhatsApp notifications."""
from __future__ import annotations

import os
from datetime import date as date_cls, datetime, time as time_cls
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from extensions import db
from models import MaterialReceiptNote, Supplier
from whatsapp import WhatsAppError, wa_send_text_lk

bp = Blueprint("mrn", __name__, url_prefix="/api/mrn")


def _parse_date(value: Any) -> date_cls | None:
    if value is None or value == "":
        return None
    if isinstance(value, date_cls):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    raise ValueError("Invalid date value")


def _parse_time(value: Any) -> time_cls | None:
    if value is None or value == "":
        return None
    if isinstance(value, time_cls):
        return value
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, str):
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue
        return datetime.fromisoformat(value).time()
    raise ValueError("Invalid time value")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _quantize(value: Decimal | None, places: int) -> Decimal | None:
    if value is None:
        return None
    quant = Decimal(f"1e-{places}")
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _format_decimal(value: Decimal | None, places: int, with_commas: bool = False) -> str:
    if value is None:
        value = Decimal(0)
    quantized = _quantize(_to_decimal(value), places) or Decimal(0)
    fmt = f"{{:,.{places}f}}" if with_commas else f"{{:.{places}f}}"
    return fmt.format(quantized)


def _build_whatsapp_message(mrn: MaterialReceiptNote, supplier: Supplier) -> str:
    qty_text = _format_decimal(mrn.qty_ton, 3)
    unit_price_text = _format_decimal(mrn.unit_price, 2, with_commas=True)
    wet_factor_text = _format_decimal(mrn.wet_factor, 3)
    approved_unit_price_text = _format_decimal(mrn.approved_unit_price, 2, with_commas=True)
    amount_text = _format_decimal(mrn.amount, 2, with_commas=True)
    mrn_date = mrn.date.isoformat() if mrn.date else ""

    return (
        "📦 Samprox ERP – MRN Confirmation\n"
        f"MRN No: {mrn.mrn_no}\n"
        f"Date: {mrn_date}\n"
        f"Supplier: {supplier.name}\n"
        f"Qty (Tons): {qty_text}\n"
        f"Unit Price: Rs.{unit_price_text}\n"
        f"Wet Factor: {wet_factor_text}\n"
        f"Approved Unit Price: Rs.{approved_unit_price_text}\n"
        f"Amount: Rs.{amount_text}\n"
        "Thank you for your delivery."
    )


@bp.route("", methods=["POST"])
def create_mrn() -> Any:
    payload: Dict[str, Any] = request.get_json(silent=True) or {}

    try:
        mrn = MaterialReceiptNote(
            mrn_no=payload.get("mrn_no"),
            date=_parse_date(payload.get("date")),
            supplier_id=payload.get("supplier_id"),
            material_type=payload.get("material_type"),
            qty_ton=_to_decimal(payload.get("qty_ton")),
            unit_price=_to_decimal(payload.get("unit_price")),
            wet_factor=_to_decimal(payload.get("wet_factor")),
            approved_unit_price=_to_decimal(payload.get("approved_unit_price")),
            amount=_to_decimal(payload.get("amount")),
            weigh_slip_no=payload.get("weigh_slip_no"),
            weigh_in_time=_parse_time(payload.get("weigh_in_time")),
            weigh_out_time=_parse_time(payload.get("weigh_out_time")),
            security_officer=payload.get("security_officer"),
            authorized_person=payload.get("authorized_person"),
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    db.session.add(mrn)
    db.session.commit()

    supplier = None
    whatsapp_status = "skipped: supplier not found"
    wa_result: Dict[str, Any] | None = None

    if mrn.supplier_id:
        supplier = Supplier.query.get(mrn.supplier_id)

    if not supplier:
        whatsapp_status = "skipped: supplier not found"
    elif not supplier.primary_phone:
        whatsapp_status = "skipped: supplier has no primary phone"
    else:
        phone_number = supplier.primary_phone
        phone_env = (os.getenv("WA_PHONE_NUMBER_ID", "").strip(), os.getenv("WA_ACCESS_TOKEN", "").strip())
        if not all(phone_env):
            whatsapp_status = "skipped: WhatsApp API not configured"
        else:
            message = _build_whatsapp_message(mrn, supplier)
            try:
                wa_result = wa_send_text_lk(phone_number, message)
                whatsapp_status = "sent"
            except ValueError as exc:
                whatsapp_status = f"skipped: {exc}"  # invalid phone format
            except WhatsAppError as exc:
                whatsapp_status = f"failed: {exc}"

    response_payload: Dict[str, Any] = {
        "status": "saved",
        "whatsapp": whatsapp_status,
        "mrn_id": str(mrn.id),
    }
    if wa_result is not None:
        response_payload["wa_result"] = wa_result

    return jsonify(response_payload), 201
