"""add pruned protections (pruned_protections)

Revision ID: a9c2e4f6b318
Revises: f3b7d9a2c514
Create Date: 2026-10-25 20:30:00.000000

版の巡回で消える版の、合意の形へ戻した記録(`generated_documents.protection`)を写す表を足す
(ステージ13)。要素の履歴が、消えた版の戻しも出せるようにする。この表を足す前に消えた版の分は
無い(写しを作らない)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a9c2e4f6b318'
down_revision: Union[str, Sequence[str], None] = 'f3b7d9a2c514'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('pruned_protections',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('doc_type', sa.String(length=50), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('protection', _JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('pruned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'doc_type', 'version', name='uq_pruned_protections_project_type_version')
    )
    op.create_index(op.f('ix_pruned_protections_project_id'), 'pruned_protections', ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_pruned_protections_project_id'), table_name='pruned_protections')
    op.drop_table('pruned_protections')
