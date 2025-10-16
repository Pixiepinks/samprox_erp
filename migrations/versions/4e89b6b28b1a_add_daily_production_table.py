"""add daily production table

Revision ID: 4e89b6b28b1a
Revises: 2e172ffd71bd
Create Date: 2025-01-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4e89b6b28b1a'
down_revision = '2e172ffd71bd'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'daily_production_entry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('asset_id', sa.Integer(), nullable=False),
        sa.Column('hour_no', sa.Integer(), nullable=False),
        sa.Column('quantity_tons', sa.Float(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['asset_id'], ['machine_asset.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date', 'asset_id', 'hour_no', name='uq_daily_production_entry_day_asset_hour'),
    )
    with op.batch_alter_table('daily_production_entry', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_daily_production_entry_date'), ['date'], unique=False)
        batch_op.create_index(batch_op.f('ix_daily_production_entry_asset_id'), ['asset_id'], unique=False)


def downgrade():
    with op.batch_alter_table('daily_production_entry', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_daily_production_entry_asset_id'))
        batch_op.drop_index(batch_op.f('ix_daily_production_entry_date'))

    op.drop_table('daily_production_entry')
