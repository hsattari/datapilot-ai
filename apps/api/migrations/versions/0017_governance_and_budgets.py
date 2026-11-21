"""metric scope, classification, onboarding fields, consent, AI token usage

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('metrics', sa.Column('default_filters', sa.JSON(), nullable=True))
    op.add_column('metrics', sa.Column('effective_from', sa.Date(), nullable=True))
    op.add_column('metrics', sa.Column('effective_to', sa.Date(), nullable=True))
    op.add_column('tenants', sa.Column('business_description', sa.Text(), nullable=True))
    op.add_column('tenants', sa.Column('column_classification', sa.JSON(), nullable=True))
    op.add_column('users', sa.Column('consent_version', sa.String(40), nullable=True))
    op.add_column('users', sa.Column('consent_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('ai_usage',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False, index=True),
        sa.Column('month', sa.String(7), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('calls', sa.Integer(), nullable=False, server_default='0'),
        sa.UniqueConstraint('tenant_id', 'month', 'kind'))


def downgrade() -> None:
    op.drop_table('ai_usage')
    for t, cols in (('users', ['consent_at', 'consent_version']), ('tenants', ['column_classification', 'business_description']),
                    ('metrics', ['effective_to', 'effective_from', 'default_filters'])):
        for c in cols:
            op.drop_column(t, c)
