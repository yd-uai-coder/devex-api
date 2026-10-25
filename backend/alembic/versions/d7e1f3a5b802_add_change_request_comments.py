"""add change_request_comments

Revision ID: d7e1f3a5b802
Revises: c5d8e2f4a613
Create Date: 2026-10-25 14:00:00.000000

CR のコメント欄(ステージ13)の表を足す。クライアントが共有リンクから書いたコメントと、開発者の
返信(1段)を持つ。新しい表なので、既存のデータには触らない。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd7e1f3a5b802'
down_revision: Union[str, Sequence[str], None] = 'c5d8e2f4a613'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('change_request_comments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('change_request_id', sa.Uuid(), nullable=False),
    sa.Column('parent_id', sa.Uuid(), nullable=True),
    sa.Column('share_link_id', sa.Uuid(), nullable=True),
    sa.Column('author_kind', sa.String(length=20), nullable=False),
    sa.Column('author_name', sa.String(length=200), nullable=False),
    sa.Column('author_id', sa.Uuid(), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['change_request_id'], ['change_requests.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_id'], ['change_request_comments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(
        ['share_link_id'], ['change_request_share_links.id'], ondelete='SET NULL'
    ),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_change_request_comments_change_request_id',
        'change_request_comments',
        ['change_request_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_change_request_comments_change_request_id',
        table_name='change_request_comments',
    )
    op.drop_table('change_request_comments')
