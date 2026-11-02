"""add change_requests.predicted_impact

Revision ID: 6c43be350866
Revises: 9f3e279a0210
Create Date: 2026-10-19 18:30:00.000000

CR の判断(合意・却下)の時点の影響の予測を残す列を足す。既存の行は NULL(判断の前と同じく、
求めるたびに基準の束から作る)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '6c43be350866'
down_revision: Union[str, Sequence[str], None] = '9f3e279a0210'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('change_requests', sa.Column('predicted_impact', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('change_requests', 'predicted_impact')
