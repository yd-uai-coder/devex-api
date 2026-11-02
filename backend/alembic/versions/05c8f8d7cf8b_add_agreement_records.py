"""add agreement records (agreement_records)

Revision ID: 05c8f8d7cf8b
Revises: be106e6426be
Create Date: 2026-10-20 12:00:00.000000

要素(要件・利用者と使う場面・画面・API・外部連携)ごとの合意の記録の表を足す(ステージ12)。
記録は追記だけで、今の合意はキーごとの最新の行。既存の束からの記録は、次のマイグレーション
(`75d414ded7cb`)で作る。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '05c8f8d7cf8b'
down_revision: Union[str, Sequence[str], None] = 'be106e6426be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('agreement_records',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=300), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('content', _JSON, nullable=True),
    sa.Column('origin', sa.String(length=20), nullable=False),
    sa.Column('baseline_id', sa.Uuid(), nullable=True),
    sa.Column('change_request_id', sa.Uuid(), nullable=True),
    sa.Column('recorded_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['baseline_id'], ['baselines.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['change_request_id'], ['change_requests.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['recorded_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'seq', name='uq_agreement_records_project_seq')
    )
    op.create_index('ix_agreement_records_project_key', 'agreement_records', ['project_id', 'key'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_agreement_records_project_key', table_name='agreement_records')
    op.drop_table('agreement_records')
