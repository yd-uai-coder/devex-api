"""add confirmation_items changed_by_change_request_number

Revision ID: f3b7d9a2c514
Revises: e8f2a4b6c913
Create Date: 2026-10-25 20:00:00.000000

CR の反映で文が変わり、確認済みから未確認に戻した確認事項に、戻した CR の番号を残す
(ステージ13)。既存の行は NULL のまま(戻していない)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f3b7d9a2c514'
down_revision: Union[str, Sequence[str], None] = 'e8f2a4b6c913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'confirmation_items',
        sa.Column('changed_by_change_request_number', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('confirmation_items', 'changed_by_change_request_number')
