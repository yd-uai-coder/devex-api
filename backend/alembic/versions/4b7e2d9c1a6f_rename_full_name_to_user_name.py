"""rename users.full_name to user_name

Revision ID: 4b7e2d9c1a6f
Revises: 8d4f1a2b6c3e
Create Date: 2026-10-16 18:00:00.000000

表示名の列 `full_name` を、マイページで変える「ユーザーネーム」に合わせて `user_name` に改名する。
列の改名なので、既存の値はそのまま残る。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4b7e2d9c1a6f'
down_revision: Union[str, Sequence[str], None] = '8d4f1a2b6c3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('users', 'full_name', new_column_name='user_name')


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('users', 'user_name', new_column_name='full_name')
