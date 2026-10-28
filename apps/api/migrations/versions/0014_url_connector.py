"""url connector columns

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('data_sources', sa.Column('url', sa.String(2000), nullable=True))
    op.add_column('data_sources', sa.Column('sync_every_hours', sa.Integer(), nullable=True))
    op.add_column('data_sources', sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('data_sources', sa.Column('last_sync_error', sa.String(500), nullable=True))


def downgrade() -> None:
    for c in ('last_sync_error', 'last_sync_at', 'sync_every_hours', 'url'):
        op.drop_column('data_sources', c)
