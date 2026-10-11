"""add chat_histories.cards

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-10-09 18:00:00.000000

ヒアリングの選択肢カードを、AI の発言の行に持たせる(選ばれた選択肢も同じ JSON に書く)。
既存の行は NULL のまま(カードの無い発言)。移行の処理は無い。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('chat_histories', sa.Column('cards', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('chat_histories', 'cards')
