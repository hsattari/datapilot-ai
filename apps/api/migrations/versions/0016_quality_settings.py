"""per-organisation data-quality thresholds

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('quality_settings', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('tenants', 'quality_settings')
