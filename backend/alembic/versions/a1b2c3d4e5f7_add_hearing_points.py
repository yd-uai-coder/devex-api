"""add hearing_points and projects.hearing_scale

Revision ID: a1b2c3d4e5f7
Revises: c9d0e1f2a3b4
Create Date: 2026-10-09 12:00:00.000000

ヒアリングの観点の表(観点ごとの状態)と、ヒアリングで決まった規模を足す。
既存のプロジェクトは行を持たないまま(projects.hearing_check の5条件の判定を表示し、
次の発言から観点の表で判定する)。移行の処理は無い。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('hearing_points',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('point_key', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('evidence', _JSON, nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'point_key', name='uq_hearing_points_project_point')
    )
    op.add_column('projects', sa.Column('hearing_scale', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'hearing_scale')
    op.drop_table('hearing_points')
