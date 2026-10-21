"""data quality results and schema versions

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('schema_versions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('dataset_id', sa.String(36), sa.ForeignKey('datasets.id'), nullable=False),
        sa.Column('source_id', sa.String(36), nullable=False),
        sa.Column('dataset_name', sa.String(200), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('columns', sa.JSON(), nullable=False),
        sa.Column('changes', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    for c in ('tenant_id', 'dataset_id', 'source_id'):
        op.create_index(f'ix_schema_versions_{c}', 'schema_versions', [c])
    op.create_table('quality_results',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('dataset_id', sa.String(36), sa.ForeignKey('datasets.id'), nullable=False),
        sa.Column('stage', sa.String(20), nullable=False),
        sa.Column('rule', sa.String(100), nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('observed', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    for c in ('tenant_id', 'dataset_id'):
        op.create_index(f'ix_quality_results_{c}', 'quality_results', [c])


def downgrade() -> None:
    op.drop_table('quality_results')
    op.drop_table('schema_versions')
