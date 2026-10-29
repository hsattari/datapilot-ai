"""metric metadata

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('metrics', sa.Column('owner', sa.String(200), nullable=True))
    op.add_column('metrics', sa.Column('unit', sa.String(30), nullable=True))
    op.add_column('metrics', sa.Column('example_questions', sa.JSON(), nullable=True))


def downgrade() -> None:
    for c in ('example_questions', 'unit', 'owner'):
        op.drop_column('metrics', c)
