"""add generated_documents.protection

Revision ID: 956b403c67fe
Revises: 75d414ded7cb
Create Date: 2026-10-20 18:00:00.000000

再生成の後に合意の形へ戻した要素(`[{key, reason}]`)を版に残す列を足す(ステージ12)。
既存の版は NULL(合意を守る前に作った版)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '956b403c67fe'
down_revision: Union[str, Sequence[str], None] = '75d414ded7cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('generated_documents', sa.Column('protection', _JSON, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('generated_documents', 'protection')
