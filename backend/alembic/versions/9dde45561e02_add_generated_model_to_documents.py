"""add generated_model to generated_documents

Revision ID: 9dde45561e02
Revises: d7e2a9c4f1b3
Create Date: 2026-10-19 10:00:00.000000

文書の版に、生成したモデル名を残す(どの版を、どのモデルで作ったかを後からたどるため。合意済みの
版の写しにも載る)。既存の版は NULL(分からない)のまま。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9dde45561e02'
down_revision: Union[str, Sequence[str], None] = 'd7e2a9c4f1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'generated_documents', sa.Column('generated_model', sa.String(length=100), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('generated_documents', 'generated_model')
