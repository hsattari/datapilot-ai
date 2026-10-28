"""source refresh cadence and management summaries

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('data_sources', sa.Column('expected_every_days', sa.Integer(), nullable=True))
    op.create_table('summaries',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('period_start', sa.String(10), nullable=False),
        sa.Column('period_end', sa.String(10), nullable=False),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'kind', 'period_start'))
    op.create_index('ix_summaries_tenant_id', 'summaries', ['tenant_id'])


def downgrade() -> None:
    op.drop_table('summaries')
    op.drop_column('data_sources', 'expected_every_days')
