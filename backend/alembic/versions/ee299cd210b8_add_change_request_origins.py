"""add change_requests.origins

Revision ID: ee299cd210b8
Revises: 382a5efa3508
Create Date: 2026-10-20 21:00:00.000000

新しい要件を足す CR の予測の起点(`[{key, label}]`。束の要件・画面・API)を持つ列を足す
(ステージ12)。既存の CR は NULL(起点なし。今までどおり「予測に入っていない」)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ee299cd210b8'
down_revision: Union[str, Sequence[str], None] = '382a5efa3508'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('change_requests', sa.Column('origins', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('change_requests', 'origins')
