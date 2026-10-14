"""add snapshots to review_runs

Revision ID: 5a1c7e93d2b4
Revises: a7b8c9d0e1f2
Create Date: 2026-10-14 18:00:00.000000

設計レビューの回に、段階の区切りでレビューした段階の内容の写し(`snapshots`、
{"stage:N": 承認した model})を足す。採用した指摘の対象が変わったかを比べるため。
文書の区切りの回・この列を足す前の回は null のまま。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5a1c7e93d2b4'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('review_runs', sa.Column('snapshots', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('review_runs', 'snapshots')
