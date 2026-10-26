"""add change request conflict checks (change_requests.conflict_checks)

Revision ID: c4e8a1f7b2d6
Revises: a9c2e4f6b318
Create Date: 2026-10-26 18:00:00.000000

重なる CR の競合を「今の文と見比べて確かめた」の印(`[{key, seq}]`)を CR に持つ(ステージ14)。
反映は、未確認の競合があると断る。この列を足す前の CR は NULL(確かめていない)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c4e8a1f7b2d6'
down_revision: Union[str, Sequence[str], None] = 'a9c2e4f6b318'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('change_requests', sa.Column('conflict_checks', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('change_requests', 'conflict_checks')
