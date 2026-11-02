"""add confirmation items (confirmation_items)

Revision ID: 382a5efa3508
Revises: 956b403c67fe
Create Date: 2026-10-20 21:00:00.000000

確認事項の一覧で利用者が決めた欄(決める機会・状態・メモ)を、項目のキーごとに持つ表を足す
(ステージ12)。一覧そのものは保存しない(今の要件定義書と観点の表から毎回組み立てる)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '382a5efa3508'
down_revision: Union[str, Sequence[str], None] = '956b403c67fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('confirmation_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('key', sa.String(length=300), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('opportunity', sa.String(length=20), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('memo', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'key', name='uq_confirmation_items_project_key')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('confirmation_items')
