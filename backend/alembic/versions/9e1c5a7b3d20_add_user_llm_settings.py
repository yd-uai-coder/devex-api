"""add user_model_settings and user_api_keys

Revision ID: 9e1c5a7b3d20
Revises: 4b7e2d9c1a6f
Create Date: 2026-10-16 21:00:00.000000

マイページで登録する、用途ごとのモデル(user_id が null の行はシステムの既定値)と、provider ごとの
API キー(暗号文と末尾の4文字)の表を足す。既定値の行は scripts/seed.py が `.env` のモデルで書く。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9e1c5a7b3d20'
down_revision: Union[str, Sequence[str], None] = '4b7e2d9c1a6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'user_model_settings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('purpose', sa.String(length=20), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'purpose', name='uq_user_model_settings_user_purpose',
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f('ix_user_model_settings_user_id'), 'user_model_settings', ['user_id'], unique=False
    )
    op.create_table(
        'user_api_keys',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('encrypted_key', sa.Text(), nullable=False),
        sa.Column('key_hint', sa.String(length=8), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'provider', name='uq_user_api_keys_user_provider'),
    )
    op.create_index(
        op.f('ix_user_api_keys_user_id'), 'user_api_keys', ['user_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_user_api_keys_user_id'), table_name='user_api_keys')
    op.drop_table('user_api_keys')
    op.drop_index(op.f('ix_user_model_settings_user_id'), table_name='user_model_settings')
    op.drop_table('user_model_settings')
