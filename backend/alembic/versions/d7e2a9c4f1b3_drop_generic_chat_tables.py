"""drop generic chat tables

Revision ID: d7e2a9c4f1b3
Revises: c4f8a2e6d1b9
Create Date: 2026-10-19 10:00:00.000000

テンプレート由来の汎用チャット(`POST /chat`)を削除したので、その `conversations`・`messages`
を落とす。Devex のヒアリングは `chat_histories` を使い、この2つは使っていない。

downgrade は空のテーブルを作り直すだけで、落とした行は戻らない。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd7e2a9c4f1b3'
down_revision: Union[str, Sequence[str], None] = 'c4f8a2e6d1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table('messages')
    op.drop_table('conversations')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'conversations',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id',
            sa.Uuid(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        'messages',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            'conversation_id',
            sa.Uuid(as_uuid=True),
            sa.ForeignKey('conversations.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('role', sa.String(length=32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
