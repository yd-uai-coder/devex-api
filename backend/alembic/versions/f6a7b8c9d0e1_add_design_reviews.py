"""add design reviews (review_runs, review_findings, users.review_enabled)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d1
Create Date: 2026-10-14 00:00:00.000000

設計レビューの回(`review_runs`)と指摘(`review_findings`)の表と、ユーザーのレビューの設定
(`users.review_enabled`、初期値はオフ)を足す。既存のユーザーはすべてオフで始める。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('review_enabled', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table('review_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('gate', sa.String(length=20), nullable=False),
    sa.Column('targets', _JSON, nullable=False),
    sa.Column('trigger', sa.String(length=20), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error_code', sa.String(length=50), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('cache_read_tokens', sa.Integer(), nullable=True),
    sa.Column('cache_creation_tokens', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_review_runs_project_gate', 'review_runs', ['project_id', 'gate'])
    op.create_table('review_findings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('level', sa.String(length=20), nullable=False),
    sa.Column('cause', sa.String(length=20), nullable=False),
    sa.Column('targets', _JSON, nullable=False),
    sa.Column('point_key', sa.String(length=40), nullable=True),
    sa.Column('problem', sa.Text(), nullable=False),
    sa.Column('evidence', sa.Text(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('suggestion', sa.Text(), nullable=False),
    sa.Column('decision', sa.String(length=20), server_default='pending', nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['review_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_review_findings_run_id', 'review_findings', ['run_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_review_findings_run_id', table_name='review_findings')
    op.drop_table('review_findings')
    op.drop_index('ix_review_runs_project_gate', table_name='review_runs')
    op.drop_table('review_runs')
    op.drop_column('users', 'review_enabled')
