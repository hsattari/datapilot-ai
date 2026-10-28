"""conversations, messages, answer feedback

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('conversations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.String(120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_conversations_tenant_id', 'conversations', ['tenant_id'])
    op.create_index('ix_conversations_user_id', 'conversations', ['user_id'])
    op.create_table('conversation_messages',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('outcome', sa.String(30), nullable=False),
        sa.Column('plan', sa.JSON(), nullable=False),
        sa.Column('interpreted', sa.Text(), nullable=True),
        sa.Column('answer', sa.JSON(), nullable=False),
        sa.Column('feedback', sa.String(10), nullable=True),
        sa.Column('feedback_comment', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_conversation_messages_tenant_id', 'conversation_messages', ['tenant_id'])
    op.create_index('ix_conversation_messages_conversation_id', 'conversation_messages', ['conversation_id'])
    op.create_index('ix_conversation_messages_user_id', 'conversation_messages', ['user_id'])


def downgrade() -> None:
    op.drop_table('conversation_messages')
    op.drop_table('conversations')
