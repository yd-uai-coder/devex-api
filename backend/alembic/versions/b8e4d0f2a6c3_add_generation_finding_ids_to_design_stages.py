"""add generation_finding_ids to design_stages

Revision ID: b8e4d0f2a6c3
Revises: a7d3c9e1f5b2
Create Date: 2026-10-17 11:00:00.000000

段階の直近の生成の修正指示に入れた、設計レビューの指摘の ID を記録する。作り直した段階の画面に
「この作り直しで渡した指摘」を出すため。既存の行と、指摘を渡さない生成は null。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8e4d0f2a6c3'
down_revision: Union[str, Sequence[str], None] = 'a7d3c9e1f5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('design_stages', sa.Column('generation_finding_ids', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('design_stages', 'generation_finding_ids')
