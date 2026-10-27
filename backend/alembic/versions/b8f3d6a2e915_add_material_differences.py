"""add material differences (material_extractions, material_differences)

Revision ID: b8f3d6a2e915
Revises: e7d2b9a4c103
Create Date: 2026-10-27 15:00:00.000000

資料から取り出した違い(ステージ14)。取り出しの直近の回(資料ごとに1行。比べた束とその時点の
鮮度)と、違い1件ずつ(種類・対象・起点・内容・根拠の引用・状態)。どちらも資料と一緒に消える。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8f3d6a2e915'
down_revision: Union[str, Sequence[str], None] = 'e7d2b9a4c103'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'material_extractions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('material_id', sa.Uuid(), nullable=False),
        sa.Column('baseline_number', sa.Integer(), nullable=False),
        sa.Column('freshness', _JSON, nullable=False),
        sa.Column('generated_model', sa.String(length=100), nullable=True),
        sa.Column('generated_by', sa.Uuid(), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['generated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('material_id'),
    )
    op.create_table(
        'material_differences',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('material_id', sa.Uuid(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('target_key', sa.String(length=300), nullable=False),
        sa.Column('origin_key', sa.String(length=300), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('quote', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('reasons', _JSON, nullable=False),
        sa.Column('dismiss_reason', sa.Text(), nullable=True),
        sa.Column('baseline_number', sa.Integer(), nullable=False),
        sa.Column('decided_by', sa.Uuid(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_material_differences_material',
        'material_differences',
        ['material_id', 'position'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_material_differences_material', table_name='material_differences')
    op.drop_table('material_differences')
    op.drop_table('material_extractions')
