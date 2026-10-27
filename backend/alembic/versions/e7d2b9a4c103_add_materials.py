"""add materials (materials)

Revision ID: e7d2b9a4c103
Revises: c4e8a1f7b2d6
Create Date: 2026-10-27 10:00:00.000000

プロジェクトの途中から足す資料(議事録・メール・資料)の表(ステージ14)。作成時の添付資料
(intake_files)とは別に持ち、ヒアリングの根拠には使わない。本文は切り詰めずに持つ。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e7d2b9a4c103'
down_revision: Union[str, Sequence[str], None] = 'c4e8a1f7b2d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'materials',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('material_date', sa.Date(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_materials_project_created', 'materials', ['project_id', 'created_at'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_materials_project_created', table_name='materials')
    op.drop_table('materials')
