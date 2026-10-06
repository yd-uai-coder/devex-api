"""add generation status columns to design_stages

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, Sequence[str], None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 段階のAIの下書きの生成の状態。既存の行は「まだ生成していない」(NULL)のままでよい
    op.add_column('design_stages', sa.Column('generation_status', sa.String(length=20), nullable=True))
    op.add_column('design_stages', sa.Column('generation_error', sa.Text(), nullable=True))
    op.add_column(
        'design_stages',
        sa.Column('generation_started_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('design_stages', 'generation_started_at')
    op.drop_column('design_stages', 'generation_error')
    op.drop_column('design_stages', 'generation_status')
