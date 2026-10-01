"""add mode to projects (simple / detailed)

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
Create Date: 2026-10-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 既存のプロジェクトは簡易ドキュメントモード(今までの4文書の一括生成)として扱う
    op.add_column('projects', sa.Column('mode', sa.String(length=20), server_default='simple', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'mode')
