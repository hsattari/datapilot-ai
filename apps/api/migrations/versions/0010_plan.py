"""plan per organisation (cost control)

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('plan', sa.String(20), nullable=False, server_default='starter'))


def downgrade() -> None:
    op.drop_column('tenants', 'plan')
