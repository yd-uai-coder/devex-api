"""add change request verification

Revision ID: 483d9e531ba0
Revises: f9c9614d5329
Create Date: 2026-10-20 10:00:00.000000

変更要求(CR)の再検証とたどれる記録の列と表を足す。CR に文書の側の確認済みのキー
(`document_checks`)と、検証済みの後に入れた合意済みの版(`included_baseline_id`)、CR に結んだ
設計レビューの回(`change_request_reviews`)。列はどれも NULL 可で、既存の行は NULL。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '483d9e531ba0'
down_revision: Union[str, Sequence[str], None] = 'f9c9614d5329'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('change_requests', sa.Column('document_checks', _JSON, nullable=True))
    op.add_column('change_requests', sa.Column('included_baseline_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'change_requests_included_baseline_id_fkey', 'change_requests', 'baselines',
        ['included_baseline_id'], ['id'], ondelete='SET NULL',
    )
    op.create_table(
        'change_request_reviews',
        sa.Column('change_request_id', sa.Uuid(), nullable=False),
        sa.Column('review_run_id', sa.Uuid(), nullable=False),
        sa.Column('linked_by', sa.Uuid(), nullable=True),
        sa.Column('linked_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['change_request_id'], ['change_requests.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(['review_run_id'], ['review_runs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['linked_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('change_request_id', 'review_run_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('change_request_reviews')
    op.drop_constraint(
        'change_requests_included_baseline_id_fkey', 'change_requests', type_='foreignkey'
    )
    op.drop_column('change_requests', 'included_baseline_id')
    op.drop_column('change_requests', 'document_checks')
