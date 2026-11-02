"""add change_request_id to material_differences

Revision ID: d5a1c7e3f926
Revises: b8f3d6a2e915
Create Date: 2026-10-28 10:00:00.000000

違いから作った CR(ステージ14)。CR にした違いが、どの CR になったかを持つ(CR はプロジェクトの
削除でだけ消えるが、消えたときは結びを外して違いを残す)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd5a1c7e3f926'
down_revision: Union[str, Sequence[str], None] = 'b8f3d6a2e915'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'material_differences', sa.Column('change_request_id', sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        'material_differences_change_request_id_fkey', 'material_differences', 'change_requests',
        ['change_request_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'material_differences_change_request_id_fkey', 'material_differences',
        type_='foreignkey',
    )
    op.drop_column('material_differences', 'change_request_id')
