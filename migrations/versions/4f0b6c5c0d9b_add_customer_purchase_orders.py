"""add customer purchase order tables

Revision ID: 4f0b6c5c0d9b
Revises: d3a225da1d16
Create Date: 2024-06-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '4f0b6c5c0d9b'
down_revision = 'd3a225da1d16'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'customer_purchase_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('po_number', sa.String(length=40), nullable=False),
        sa.Column('po_date', sa.Date(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('customer_reference', sa.String(length=120), nullable=True),
        sa.Column('delivery_address', sa.Text(), nullable=True),
        sa.Column('delivery_date', sa.Date(), nullable=True),
        sa.Column('payment_terms', sa.String(length=120), nullable=True),
        sa.Column('sales_rep_id', sa.Integer(), nullable=True),
        sa.Column('contact_person', sa.String(length=120), nullable=True),
        sa.Column('contact_phone', sa.String(length=80), nullable=True),
        sa.Column('contact_email', sa.String(length=120), nullable=True),
        sa.Column('subtotal_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('discount_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('vat_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('other_charges', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('grand_total', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('advance_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('outstanding_amount', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('status', sa.Enum('Draft', 'Confirmed', 'Partially Delivered', 'Fully Delivered', 'Cancelled', name='customer_purchase_order_status'), nullable=False, server_default='Draft'),
        sa.Column('internal_notes', sa.Text(), nullable=True),
        sa.Column('customer_notes', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], ),
        sa.ForeignKeyConstraint(['sales_rep_id'], ['team_member.id'], ),
        sa.ForeignKeyConstraint(['updated_by_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('po_number')
    )
    op.create_index(op.f('ix_customer_purchase_orders_customer_id'), 'customer_purchase_orders', ['customer_id'], unique=False)
    op.create_index(op.f('ix_customer_purchase_orders_is_deleted'), 'customer_purchase_orders', ['is_deleted'], unique=False)
    op.create_index(op.f('ix_customer_purchase_orders_po_date'), 'customer_purchase_orders', ['po_date'], unique=False)
    op.create_table(
        'customer_purchase_order_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('customer_po_id', sa.Integer(), nullable=False),
        sa.Column('item_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('item_code', sa.String(length=120), nullable=False),
        sa.Column('item_name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('qty_ordered', sa.Numeric(14, 3), nullable=False),
        sa.Column('unit', sa.String(length=40), nullable=False),
        sa.Column('unit_price', sa.Numeric(14, 2), nullable=False),
        sa.Column('discount_percent', sa.Numeric(6, 2), nullable=False, server_default='0'),
        sa.Column('line_total', sa.Numeric(14, 2), nullable=False),
        sa.Column('qty_delivered', sa.Numeric(14, 3), nullable=False, server_default='0'),
        sa.Column('qty_balance', sa.Numeric(14, 3), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['customer_po_id'], ['customer_purchase_orders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['item_id'], ['material_items.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_customer_purchase_order_items_customer_po_id'), 'customer_purchase_order_items', ['customer_po_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_customer_purchase_order_items_customer_po_id'), table_name='customer_purchase_order_items')
    op.drop_table('customer_purchase_order_items')
    op.drop_index(op.f('ix_customer_purchase_orders_po_date'), table_name='customer_purchase_orders')
    op.drop_index(op.f('ix_customer_purchase_orders_is_deleted'), table_name='customer_purchase_orders')
    op.drop_index(op.f('ix_customer_purchase_orders_customer_id'), table_name='customer_purchase_orders')
    op.drop_table('customer_purchase_orders')
    op.execute('DROP TYPE IF EXISTS customer_purchase_order_status')
