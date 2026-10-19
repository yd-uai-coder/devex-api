"""add change requests (change_requests, change_request_events)

Revision ID: e1cfb47f1d13
Revises: d28026a1521b
Create Date: 2026-10-19 15:00:00.000000

変更要求(CR。`change_requests`)と、その時系列(`change_request_events`。作成・状態の遷移・写し)
の表を足す(ステージ11)。既存のデータには触らない。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e1cfb47f1d13'
down_revision: Union[str, Sequence[str], None] = 'd28026a1521b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('change_requests',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('background_source', sa.String(length=20), nullable=False),
    sa.Column('background_date', sa.Date(), nullable=True),
    sa.Column('background_memo', sa.Text(), nullable=False),
    sa.Column('target_requirement_ids', _JSON, nullable=False),
    sa.Column('adds_requirement', sa.Boolean(), nullable=False),
    sa.Column('baseline_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('derived_from_id', sa.Uuid(), nullable=True),
    sa.Column('decision_counterpart', sa.String(length=200), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_memo', sa.Text(), nullable=True),
    sa.Column('decision_reason', sa.Text(), nullable=True),
    sa.Column('decision_recorded_by', sa.Uuid(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['baseline_id'], ['baselines.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['derived_from_id'], ['change_requests.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['decision_recorded_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'number', name='uq_change_requests_project_number')
    )
    op.create_table('change_request_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('change_request_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('from_status', sa.String(length=20), nullable=True),
    sa.Column('to_status', sa.String(length=20), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('note', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['change_request_id'], ['change_requests.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('change_request_id', 'position', name='uq_change_request_events_pos')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('change_request_events')
    op.drop_table('change_requests')
