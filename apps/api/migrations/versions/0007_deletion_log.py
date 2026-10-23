"""deletion log (proof of erasure, no personal data)

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('deletion_log',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('requested_by', sa.String(36), nullable=False),
        sa.Column('counts', sa.JSON(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_deletion_log_tenant_id', 'deletion_log', ['tenant_id'])


def downgrade() -> None:
    op.drop_table('deletion_log')
