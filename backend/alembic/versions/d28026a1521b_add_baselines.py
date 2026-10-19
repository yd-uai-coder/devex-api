"""add baselines (baselines, baseline_items)

Revision ID: d28026a1521b
Revises: 9dde45561e02
Create Date: 2026-10-19 11:00:00.000000

合意済みの版(束。`baselines`)と、その写し(`baseline_items`。文書・段階ごと)の表を足す
(ステージ11)。束は作った後に直さず消さないので、既存のデータには触らない。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd28026a1521b'
down_revision: Union[str, Sequence[str], None] = '9dde45561e02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('baselines',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('counterpart', sa.String(length=200), nullable=True),
    sa.Column('agreed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('memo', sa.Text(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('recorded_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['recorded_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'number', name='uq_baselines_project_number')
    )
    op.create_table('baseline_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('baseline_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=30), nullable=False),
    sa.Column('included', sa.Boolean(), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('source_version', sa.Integer(), nullable=True),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('model', _JSON, nullable=True),
    sa.Column('generated_model', sa.String(length=100), nullable=True),
    sa.ForeignKeyConstraint(['baseline_id'], ['baselines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('baseline_id', 'key', name='uq_baseline_items_key')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('baseline_items')
    op.drop_table('baselines')
