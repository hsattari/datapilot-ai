"""saved reports and metric alerts

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('reports',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('plan', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_reports_tenant_id', 'reports', ['tenant_id'])
    op.create_table('alerts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('metric', sa.String(100), nullable=False),
        sa.Column('comparison', sa.String(10), nullable=False),
        sa.Column('threshold', sa.Float(), nullable=False),
        sa.Column('last_value', sa.Float(), nullable=True),
        sa.Column('last_state', sa.String(20), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_alerts_tenant_id', 'alerts', ['tenant_id'])


def downgrade() -> None:
    op.drop_table('alerts')
    op.drop_table('reports')
