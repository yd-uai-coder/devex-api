"""add change_requests.options and change_request_option_drafts

Revision ID: a7c3e5d91f20
Revises: ee299cd210b8
Create Date: 2026-10-22 21:00:00.000000

CR の選択肢(2〜3の案と、おすすめ。`{items, recommended, reason}`)の列と、合意で選んだ案の番号の
列を足す(ステージ13)。既存の CR は NULL(案なし・選んだ案なし。今までどおり)。あわせて、案の LLM の
下書き(CR ごとに1行)の表を足す。新しい表なので、既存のデータには触らない。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7c3e5d91f20'
down_revision: Union[str, Sequence[str], None] = 'ee299cd210b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('change_requests', sa.Column('options', _JSON, nullable=True))
    op.add_column('change_requests', sa.Column('decision_option', sa.Integer(), nullable=True))
    op.create_table('change_request_option_drafts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('change_request_id', sa.Uuid(), nullable=False),
    sa.Column('options', _JSON, nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('generated_model', sa.String(length=100), nullable=True),
    sa.Column('generated_by', sa.Uuid(), nullable=True),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('adopted_by', sa.Uuid(), nullable=True),
    sa.Column('adopted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['change_request_id'], ['change_requests.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['adopted_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['generated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('change_request_id', name='uq_change_request_option_drafts_cr')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('change_request_option_drafts')
    op.drop_column('change_requests', 'decision_option')
    op.drop_column('change_requests', 'options')
