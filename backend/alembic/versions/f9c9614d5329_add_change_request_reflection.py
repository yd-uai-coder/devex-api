"""add change request reflection columns

Revision ID: f9c9614d5329
Revises: fee4fbc2a00c
Create Date: 2026-10-19 23:00:00.000000

合意した変更要求(CR)を再生成に渡した記録の列を足す。文書の版に反映した CR、段階に直近の生成の
CR と生成したモデル名、CR に反映のときに付いた印の写し、時系列の行に指すもの(作った版など)。
どれも NULL 可で、既存の行は NULL(CR を渡していない・記録の前)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f9c9614d5329'
down_revision: Union[str, Sequence[str], None] = 'fee4fbc2a00c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('generated_documents', sa.Column('change_request_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'generated_documents_change_request_id_fkey', 'generated_documents', 'change_requests',
        ['change_request_id'], ['id'], ondelete='SET NULL',
    )
    op.add_column(
        'design_stages', sa.Column('generation_change_request_id', sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        'design_stages_generation_change_request_id_fkey', 'design_stages', 'change_requests',
        ['generation_change_request_id'], ['id'], ondelete='SET NULL',
    )
    op.add_column(
        'design_stages', sa.Column('generated_model', sa.String(length=100), nullable=True)
    )
    op.add_column('change_requests', sa.Column('applied_impact', _JSON, nullable=True))
    op.add_column('change_request_events', sa.Column('refs', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('change_request_events', 'refs')
    op.drop_column('change_requests', 'applied_impact')
    op.drop_column('design_stages', 'generated_model')
    op.drop_constraint(
        'design_stages_generation_change_request_id_fkey', 'design_stages', type_='foreignkey'
    )
    op.drop_column('design_stages', 'generation_change_request_id')
    op.drop_constraint(
        'generated_documents_change_request_id_fkey', 'generated_documents', type_='foreignkey'
    )
    op.drop_column('generated_documents', 'change_request_id')
