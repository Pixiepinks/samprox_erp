from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Iterable

from flask import Blueprint, redirect, render_template, request, url_for
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from sqlalchemy import and_, func
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    Customer,
    CustomerPurchaseOrder,
    CustomerPurchaseOrderItem,
    CustomerPurchaseOrderStatus,
    MaterialItem,
    TeamMember,
    User,
)


bp = Blueprint("customer_pos", __name__, url_prefix="/customer-pos")


def _current_user() -> User | None:
    try:
        verify_jwt_in_request(optional=True)
    except Exception:  # pragma: no cover - defensive guard
        return None

    identity = get_jwt_identity()
    if not identity:
        return None

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return None

    return User.query.get(user_id)


def _load_dropdowns() -> dict[str, Iterable]:
    return {
        "customers": Customer.query.order_by(Customer.name.asc()).all(),
        "item_options": MaterialItem.query.filter_by(is_active=True).order_by(MaterialItem.name.asc()).all(),
        "team_members": TeamMember.query.order_by(TeamMember.name.asc()).all(),
        "statuses": list(CustomerPurchaseOrderStatus),
    }


def _parse_decimal(value: str | None, digits: int = 2) -> Decimal:
    try:
        quantize_str = "1." + ("0" * digits)
        return Decimal(value or "0").quantize(Decimal(quantize_str))
    except Exception:
        return Decimal("0")


def _generate_po_number(po_date: date) -> str:
    date_part = po_date.strftime("%Y%m%d")
    count = (
        CustomerPurchaseOrder.query.filter(func.date(CustomerPurchaseOrder.po_date) == po_date)
        .with_entities(func.count())
        .scalar()
        or 0
    )

    while True:
        count += 1
        po_number = f"PO-{date_part}-{count:04d}"
        if not CustomerPurchaseOrder.query.filter_by(po_number=po_number).first():
            return po_number


def _build_items_from_form(po: CustomerPurchaseOrder | None) -> list[CustomerPurchaseOrderItem]:
    item_ids = request.form.getlist("item_id")
    descriptions = request.form.getlist("description")
    qtys = request.form.getlist("qty_ordered")
    units = request.form.getlist("unit")
    prices = request.form.getlist("unit_price")
    discounts = request.form.getlist("discount_percent")

    items: list[CustomerPurchaseOrderItem] = []
    for idx, item_id in enumerate(item_ids):
        try:
            material_item = MaterialItem.query.get(item_id)
        except Exception:
            material_item = None

        if material_item is None:
            continue

        qty = _parse_decimal(qtys[idx] if idx < len(qtys) else "0", digits=3)
        price = _parse_decimal(prices[idx] if idx < len(prices) else "0")
        discount_pct = _parse_decimal(discounts[idx] if idx < len(discounts) else "0")
        if qty <= 0 or price <= 0:
            continue

        line_total = (qty * price) * (Decimal("1") - (discount_pct / Decimal("100")))
        item = CustomerPurchaseOrderItem(
            purchase_order=po,
            item=material_item,
            item_code=str(material_item.name or material_item.id),
            item_name=material_item.name,
            description=descriptions[idx] if idx < len(descriptions) else None,
            qty_ordered=qty,
            unit=units[idx] if idx < len(units) else "",
            unit_price=price,
            discount_percent=discount_pct,
            line_total=line_total,
            qty_delivered=Decimal("0"),
            qty_balance=qty,
        )
        items.append(item)

    return items


def _bind_header_from_form(po: CustomerPurchaseOrder) -> None:
    po_date_raw = request.form.get("po_date") or date.today().isoformat()
    try:
        po.po_date = datetime.strptime(po_date_raw, "%Y-%m-%d").date()
    except ValueError:
        po.po_date = date.today()

    po.customer_id = int(request.form.get("customer_id", "0") or 0)
    po.customer_reference = request.form.get("customer_reference") or None
    po.delivery_address = request.form.get("delivery_address") or None
    po.delivery_date = None
    delivery_date_raw = request.form.get("delivery_date")
    if delivery_date_raw:
        try:
            po.delivery_date = datetime.strptime(delivery_date_raw, "%Y-%m-%d").date()
        except ValueError:
            po.delivery_date = None

    po.payment_terms = request.form.get("payment_terms") or None
    po.sales_rep_id = int(request.form.get("sales_rep_id") or 0) or None
    po.contact_person = request.form.get("contact_person") or None
    po.contact_phone = request.form.get("contact_phone") or None
    po.contact_email = request.form.get("contact_email") or None
    po.discount_amount = _parse_decimal(request.form.get("discount_amount"))
    po.vat_amount = _parse_decimal(request.form.get("vat_amount"))
    po.other_charges = _parse_decimal(request.form.get("other_charges"))
    po.advance_amount = _parse_decimal(request.form.get("advance_amount"))
    po.internal_notes = request.form.get("internal_notes") or None
    po.customer_notes = request.form.get("customer_notes") or None

    action = request.form.get("action") or "draft"
    desired_status = CustomerPurchaseOrderStatus.draft
    if action == "confirm":
        desired_status = CustomerPurchaseOrderStatus.confirmed

    status_raw = request.form.get("status")
    if status_raw:
        try:
            desired_status = CustomerPurchaseOrderStatus(status_raw)
        except ValueError:
            desired_status = CustomerPurchaseOrderStatus.draft

    po.status = desired_status


def _recalculate_totals(po: CustomerPurchaseOrder, items: list[CustomerPurchaseOrderItem]) -> None:
    subtotal = sum((item.line_total for item in items), Decimal("0"))
    po.subtotal_amount = subtotal
    po.grand_total = subtotal + po.vat_amount + po.other_charges - po.discount_amount
    po.outstanding_amount = po.grand_total - po.advance_amount


@bp.get("")
def list_purchase_orders():
    filters = []
    date_from_raw = request.args.get("date_from")
    date_to_raw = request.args.get("date_to")
    customer_id = request.args.get("customer_id")
    status = request.args.get("status")

    if date_from_raw:
        try:
            date_from = datetime.strptime(date_from_raw, "%Y-%m-%d").date()
            filters.append(CustomerPurchaseOrder.po_date >= date_from)
        except ValueError:
            pass

    if date_to_raw:
        try:
            date_to = datetime.strptime(date_to_raw, "%Y-%m-%d").date()
            filters.append(CustomerPurchaseOrder.po_date <= date_to)
        except ValueError:
            pass

    if customer_id:
        try:
            filters.append(CustomerPurchaseOrder.customer_id == int(customer_id))
        except ValueError:
            pass

    if status:
        try:
            status_enum = CustomerPurchaseOrderStatus(status)
            filters.append(CustomerPurchaseOrder.status == status_enum)
        except ValueError:
            pass

    query = CustomerPurchaseOrder.query.options(joinedload(CustomerPurchaseOrder.customer)).filter(
        CustomerPurchaseOrder.is_deleted.is_(False)
    )

    if filters:
        query = query.filter(and_(*filters))

    orders = query.order_by(CustomerPurchaseOrder.po_date.desc(), CustomerPurchaseOrder.id.desc()).all()
    return render_template("customer_pos/list.html", orders=orders, **_load_dropdowns())


@bp.get("/new")
def new_purchase_order():
    today = date.today()
    draft_po = CustomerPurchaseOrder(po_date=today, status=CustomerPurchaseOrderStatus.draft)
    draft_po.po_number = _generate_po_number(today)
    return render_template(
        "customer_pos/form.html",
        po=draft_po,
        po_items=[],
        is_edit_mode=False,
        **_load_dropdowns(),
    )


@bp.post("")
def create_purchase_order():
    po = CustomerPurchaseOrder()
    _bind_header_from_form(po)
    po.po_number = _generate_po_number(po.po_date)

    order_items = _build_items_from_form(po)
    if not po.customer_id or not order_items:
        return redirect(url_for("customer_pos.new_purchase_order"))

    _recalculate_totals(po, order_items)
    current_user = _current_user()
    if current_user:
        po.created_by = current_user
        po.updated_by = current_user

    db.session.add(po)
    for item in order_items:
        db.session.add(item)
    db.session.commit()
    return redirect(url_for("customer_pos.list_purchase_orders"))


@bp.get("/<int:po_id>/edit")
def edit_purchase_order(po_id: int):
    po = CustomerPurchaseOrder.query.options(joinedload(CustomerPurchaseOrder.items)).get_or_404(po_id)
    if po.is_deleted:
        return redirect(url_for("customer_pos.list_purchase_orders"))

    return render_template(
        "customer_pos/form.html",
        po=po,
        po_items=po.items,
        is_edit_mode=True,
        **_load_dropdowns(),
    )


@bp.post("/<int:po_id>")
def update_purchase_order(po_id: int):
    po = CustomerPurchaseOrder.query.options(joinedload(CustomerPurchaseOrder.items)).get_or_404(po_id)
    if po.status in {
        CustomerPurchaseOrderStatus.fully_delivered,
        CustomerPurchaseOrderStatus.cancelled,
    }:
        return redirect(url_for("customer_pos.edit_purchase_order", po_id=po.id))

    _bind_header_from_form(po)
    order_items = _build_items_from_form(po)
    if not po.customer_id or not order_items:
        return redirect(url_for("customer_pos.edit_purchase_order", po_id=po.id))

    po.items[:] = order_items
    _recalculate_totals(po, order_items)
    current_user = _current_user()
    if current_user:
        po.updated_by = current_user

    db.session.commit()
    return redirect(url_for("customer_pos.list_purchase_orders"))


@bp.post("/<int:po_id>/status")
def update_purchase_order_status(po_id: int):
    po = CustomerPurchaseOrder.query.get_or_404(po_id)
    if po.is_deleted:
        return redirect(url_for("customer_pos.list_purchase_orders"))

    status_raw = request.form.get("status") or po.status
    try:
        new_status = CustomerPurchaseOrderStatus(status_raw)
    except ValueError:
        return redirect(url_for("customer_pos.list_purchase_orders"))

    if po.status == CustomerPurchaseOrderStatus.cancelled:
        return redirect(url_for("customer_pos.list_purchase_orders"))

    po.status = new_status
    po.updated_at = datetime.utcnow()
    current_user = _current_user()
    if current_user:
        po.updated_by = current_user
    db.session.commit()
    return redirect(url_for("customer_pos.list_purchase_orders"))


@bp.post("/<int:po_id>/delete")
def delete_purchase_order(po_id: int):
    po = CustomerPurchaseOrder.query.get_or_404(po_id)
    po.is_deleted = True
    po.updated_at = datetime.utcnow()
    current_user = _current_user()
    if current_user:
        po.updated_by = current_user
    db.session.commit()
    return redirect(url_for("customer_pos.list_purchase_orders"))
