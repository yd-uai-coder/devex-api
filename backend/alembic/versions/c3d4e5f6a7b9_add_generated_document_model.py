"""add generated_documents.model

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-10-11 10:00:00.000000

要件定義書の意味モデル(要件の行・ID・出どころ・観点)を、版ごとに持たせる。既存の版は NULL の
まま(md のまま使う)。移行の処理は無い。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b9'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('generated_documents', sa.Column('model', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('generated_documents', 'model')
