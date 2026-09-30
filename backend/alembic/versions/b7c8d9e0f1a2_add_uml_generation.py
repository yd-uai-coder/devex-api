"""add uml generation (subject/scope/generation_status on uml_diagrams, uml_generation_runs)

Revision ID: b7c8d9e0f1a2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('uml_diagrams', sa.Column('subject', sa.String(length=255), server_default='', nullable=False))
    op.add_column('uml_diagrams', sa.Column('scope', _JSON, nullable=True))
    op.add_column('uml_diagrams', sa.Column('generation_status', sa.String(length=20), server_default='completed', nullable=False))
    op.add_column('uml_diagrams', sa.Column('generation_error', sa.Text(), nullable=True))
    # Phase 8〜9のプレースホルダー(POST /diagrams)は同じ記法の空の図を何枚でも作れたため、
    # 一意制約を張る前に(project_id, notation)ごとに最新の1枚だけを残す(subjectは全て'')。
    op.execute(
        """
        DELETE FROM uml_diagrams a USING uml_diagrams b
        WHERE a.project_id = b.project_id AND a.notation = b.notation
          AND (a.updated_at < b.updated_at OR (a.updated_at = b.updated_at AND a.id < b.id))
        """
    )
    op.create_unique_constraint(
        'uq_uml_diagrams_project_notation_subject', 'uml_diagrams', ['project_id', 'notation', 'subject']
    )
    op.create_table('uml_generation_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('notation', sa.String(length=50), nullable=False),
    sa.Column('requested', _JSON, nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('results', _JSON, nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('uml_generation_runs')
    op.drop_constraint('uq_uml_diagrams_project_notation_subject', 'uml_diagrams', type_='unique')
    op.drop_column('uml_diagrams', 'generation_error')
    op.drop_column('uml_diagrams', 'generation_status')
    op.drop_column('uml_diagrams', 'scope')
    op.drop_column('uml_diagrams', 'subject')
