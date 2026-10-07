"""allow design stage 8 (implementation procedure)

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-07 18:00:00.000000

詳細設計モードの段階に、段階8(実装手順書)を足す。段階番号の CHECK を 1〜8 に広げる。
downgrade は段階8の行を消してから、CHECK を 1〜7 に戻す(行が残っていると制約を張れないため)。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = 'ck_design_stages_stage_range'


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(CONSTRAINT, 'design_stages', type_='check')
    op.create_check_constraint(CONSTRAINT, 'design_stages', 'stage BETWEEN 1 AND 8')


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('DELETE FROM design_stages WHERE stage = 8')
    op.drop_constraint(CONSTRAINT, 'design_stages', type_='check')
    op.create_check_constraint(CONSTRAINT, 'design_stages', 'stage BETWEEN 1 AND 7')
