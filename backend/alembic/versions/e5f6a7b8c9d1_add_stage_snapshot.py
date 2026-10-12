"""add approval snapshot and impact marks to design stages

Revision ID: e5f6a7b8c9d1
Revises: d4e5f6a7b8c0
Create Date: 2026-10-11 18:00:00.000000

段階に、直前に承認したときの model の写し(`approved_model`)と、変更の影響の印(`impact`)の列を足す。

- 写しは、承認済みで承認の後に変わっていない行(`status='approved'`かつ`version = approved_version`)
  にだけ、今の`model`を入れる。承認の後に編集した行は、承認したときの内容が残っていないので写しを
  持たない(その段階の差分は取らず、今までどおり段階の「古い」だけで扱う)。
- 印は空で始める(この移行より前の変更の影響は求めない)。

変換は、このファイルの中の純粋関数で行い、app のコードを import しない(後の改修で、この移行の
結果が変わらないようにするため)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d1'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_json = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
_stages = sa.table(
    'design_stages',
    sa.column('id', sa.Uuid()),
    sa.column('status', sa.String()),
    sa.column('model', _json),
    sa.column('version', sa.Integer()),
    sa.column('approved_version', sa.Integer()),
    sa.column('approved_model', _json),
)


def snapshot_of(
    status: str, model: dict | None, version: int, approved_version: int | None
) -> dict | None:
    """行の写し。承認済みで、承認の後に変わっていない行だけが今の model を写しにする。"""
    if status != 'approved' or approved_version != version or not model:
        return None
    return model


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('design_stages', sa.Column('approved_model', _json, nullable=True))
    op.add_column('design_stages', sa.Column('impact', _json, nullable=True))
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            _stages.c.id,
            _stages.c.status,
            _stages.c.model,
            _stages.c.version,
            _stages.c.approved_version,
        )
    ).all()
    for row in rows:
        snapshot = snapshot_of(row.status, row.model, row.version, row.approved_version)
        if snapshot is not None:
            bind.execute(
                _stages.update().where(_stages.c.id == row.id).values(approved_model=snapshot)
            )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('design_stages', 'impact')
    op.drop_column('design_stages', 'approved_model')
