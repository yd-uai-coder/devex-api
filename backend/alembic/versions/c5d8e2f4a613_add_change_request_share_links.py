"""add change_request_share_links

Revision ID: c5d8e2f4a613
Revises: a7c3e5d91f20
Create Date: 2026-10-25 10:00:00.000000

CR の共有リンク(ステージ13)の表を足す。トークンは sha256 だけを持ち、期限・取り消し・最後に
見られた日時を持つ。新しい表なので、既存のデータには触らない。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c5d8e2f4a613'
down_revision: Union[str, Sequence[str], None] = 'a7c3e5d91f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('change_request_share_links',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('change_request_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.Uuid(), nullable=True),
    sa.Column('last_viewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['change_request_id'], ['change_requests.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash', name='uq_change_request_share_links_token_hash')
    )
    op.create_index(
        'ix_change_request_share_links_change_request_id',
        'change_request_share_links',
        ['change_request_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_change_request_share_links_change_request_id',
        table_name='change_request_share_links',
    )
    op.drop_table('change_request_share_links')
