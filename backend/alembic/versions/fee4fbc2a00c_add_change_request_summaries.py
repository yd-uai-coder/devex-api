"""add change_request_summaries

Revision ID: fee4fbc2a00c
Revises: 6c43be350866
Create Date: 2026-10-19 21:00:00.000000

変更の説明書の任意の要約(CR ごとに1行)の表を足す。LLM の元の文と人が直した文、未確認 / 確認済み、
作った・確かめた時点の説明書の fingerprint を持つ。新しい表なので、既存のデータには触らない。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'fee4fbc2a00c'
down_revision: Union[str, Sequence[str], None] = '6c43be350866'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('change_request_summaries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('change_request_id', sa.Uuid(), nullable=False),
    sa.Column('generated_text', sa.Text(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('generated_model', sa.String(length=100), nullable=True),
    sa.Column('generated_by', sa.Uuid(), nullable=True),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmed_by', sa.Uuid(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['change_request_id'], ['change_requests.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['generated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('change_request_id', name='uq_change_request_summaries_cr')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('change_request_summaries')
