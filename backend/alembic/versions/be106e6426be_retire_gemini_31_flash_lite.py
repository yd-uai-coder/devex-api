"""retire gemini-3.1-flash-lite

Revision ID: be106e6426be
Revises: 483d9e531ba0
Create Date: 2026-10-20 12:00:00.000000

選べるモデルの表から外したモデルを選んでいる利用者の設定(と既定値の行)を、公式の後継へ移す。
表に無いモデルは呼び出しが失敗するため。過去の版・要約の `generated_model` は記録なので変えない。
downgrade は何もしない(移す前にどの行が外したモデルだったかは分からないため)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'be106e6426be'
down_revision: Union[str, Sequence[str], None] = '483d9e531ba0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 外したモデル → 後継(Gemini API の deprecations ページの推奨。2026-10-20 に確かめた)
RETIRED: dict[str, str] = {'gemini-3.1-flash-lite': 'gemini-3.5-flash-lite'}


def upgrade() -> None:
    """Upgrade schema."""
    for old, new in RETIRED.items():
        op.execute(
            sa.text('UPDATE user_model_settings SET model = :new WHERE model = :old').bindparams(
                old=old, new=new
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
