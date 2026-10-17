"""add google_sub to users

Revision ID: c4f8a2e6d1b9
Revises: b8e4d0f2a6c3
Create Date: 2026-10-18 10:00:00.000000

Google でのログインのため、Google アカウントの識別子(ID トークンの sub)を持たせ、パスワードを
null 可にする(Google だけで登録した利用者はパスワードを持たない)。

downgrade は、パスワードが null の行があると NOT NULL に戻せずに失敗する。戻す前に
`SELECT count(*) FROM users WHERE hashed_password IS NULL` で 0 件であることを確かめる。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4f8a2e6d1b9'
down_revision: Union[str, Sequence[str], None] = 'b8e4d0f2a6c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('google_sub', sa.String(length=255), nullable=True))
    op.create_index(op.f('ix_users_google_sub'), 'users', ['google_sub'], unique=True)
    op.alter_column(
        'users', 'hashed_password', existing_type=sa.String(length=255), nullable=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'users', 'hashed_password', existing_type=sa.String(length=255), nullable=False
    )
    op.drop_index(op.f('ix_users_google_sub'), table_name='users')
    op.drop_column('users', 'google_sub')
