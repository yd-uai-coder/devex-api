"""add reopened_after to hearing_points

Revision ID: 6b2d8fa4e3c5
Revises: 5a1c7e93d2b4
Create Date: 2026-10-14 18:30:00.000000

設計レビューの指摘で観点を未決に戻したときの、聞き直しの発言(`reopened_after`、
chat_histories.id の文字列)を足す。それより後の利用者の発言を根拠にしないと、観点を決まったことに
戻さない。既存の行は null(未決に戻していない)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '6b2d8fa4e3c5'
down_revision: Union[str, Sequence[str], None] = '5a1c7e93d2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('hearing_points', sa.Column('reopened_after', sa.String(length=36), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('hearing_points', 'reopened_after')
