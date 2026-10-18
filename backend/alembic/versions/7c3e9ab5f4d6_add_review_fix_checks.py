"""add review_fix_checks

Revision ID: 7c3e9ab5f4d6
Revises: 6b2d8fa4e3c5
Create Date: 2026-10-14 19:00:00.000000

採用した指摘が直ったかの判定(狭い判定のモデル Claude Haiku。シャドーモード)の記録を足す。
判定したときの版(`targets`)・判定(`verdict`。失敗は null と `error_code`)・理由・モデル・
トークンを持つ。指摘を消すと一緒に消える。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7c3e9ab5f4d6'
down_revision: Union[str, Sequence[str], None] = '6b2d8fa4e3c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'review_fix_checks',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('finding_id', sa.Uuid(), nullable=False),
        sa.Column('targets', _JSON, nullable=False),
        sa.Column('verdict', sa.String(length=20), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(['finding_id'], ['review_findings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_review_fix_checks_finding_id', 'review_fix_checks', ['finding_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_review_fix_checks_finding_id', table_name='review_fix_checks')
    op.drop_table('review_fix_checks')
