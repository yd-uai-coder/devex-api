"""add generation_failure to projects

Revision ID: a7d3c9e1f5b2
Revises: 9e1c5a7b3d20
Create Date: 2026-10-17 10:00:00.000000

直近の設計書・テスト仕様書の生成が失敗した記録(理由のコード・文言・作り直そうとした文書・日時)を
プロジェクトに持つ。画面が失敗を知らせるために返し、次の生成を受け付けたら消す。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7d3c9e1f5b2'
down_revision: Union[str, Sequence[str], None] = '9e1c5a7b3d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('generation_failure', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'generation_failure')
