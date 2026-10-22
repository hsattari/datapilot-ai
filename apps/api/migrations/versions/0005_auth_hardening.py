"""login lockout, MFA and user deactivation

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('disabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('failed_logins', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('mfa_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('mfa_secret', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('mfa_recovery', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('users', sa.Column('mfa_last_step', sa.Integer(), nullable=True))


def downgrade() -> None:
    for c in ('mfa_last_step', 'mfa_recovery', 'mfa_secret', 'mfa_enabled', 'locked_until', 'failed_logins', 'disabled'):
        op.drop_column('users', c)
