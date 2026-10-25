"""add change_request decision source and fingerprint

Revision ID: e8f2a4b6c913
Revises: d7e1f3a5b802
Create Date: 2026-10-25 16:00:00.000000

CR の合意・却下の記録に、出どころ(開発者が記録 / 共有リンク)と、判断したときの説明書の指紋を
足す(ステージ13)。既存の判断済みの行は NULL のまま(出どころ不明として扱う)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e8f2a4b6c913'
down_revision: Union[str, Sequence[str], None] = 'd7e1f3a5b802'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'change_requests', sa.Column('decision_source', sa.String(length=20), nullable=True)
    )
    op.add_column(
        'change_requests', sa.Column('decision_fingerprint', sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('change_requests', 'decision_fingerprint')
    op.drop_column('change_requests', 'decision_source')
