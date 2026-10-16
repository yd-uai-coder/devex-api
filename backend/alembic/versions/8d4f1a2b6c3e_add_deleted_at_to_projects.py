"""add deleted_at to projects

Revision ID: 8d4f1a2b6c3e
Revises: 7c3e9ab5f4d6
Create Date: 2026-10-16 12:00:00.000000

プロジェクトの削除の印(`deleted_at`)を足す。削除の要求では印だけを付け(生成中のジョブが持つ
外部キーのロックとぶつからない)、取得と一覧から外す。行と子の行は、ロックが空いたときに物理削除する
(app/services/project_deletion.py)。既存の行は null(削除していない)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8d4f1a2b6c3e'
down_revision: Union[str, Sequence[str], None] = '7c3e9ab5f4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'projects', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'deleted_at')
