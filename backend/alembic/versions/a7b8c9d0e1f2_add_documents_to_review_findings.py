"""add documents to review_findings

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-14 12:00:00.000000

設計レビューの指摘に、直すときに書き換える文書の種類(`documents`、上流からの配列)を足す。
この列を足す前の指摘は null のまま(画面が対象の書き方から推定して補う)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('review_findings', sa.Column('documents', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('review_findings', 'documents')
