"""add design_stages (detailed design mode)

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-10-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('design_stages',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('stage', sa.SmallInteger(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('model', _JSON, nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('approved_version', sa.Integer(), nullable=True),
    sa.Column('input_fingerprint', _JSON, nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('stage BETWEEN 1 AND 7', name='ck_design_stages_stage_range'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'stage', name='uq_design_stages_project_stage')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('design_stages')
