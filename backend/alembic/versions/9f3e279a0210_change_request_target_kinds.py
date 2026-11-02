"""change request targets with change kinds (change_requests.targets)

Revision ID: 9f3e279a0210
Revises: e1cfb47f1d13
Create Date: 2026-10-19 18:00:00.000000

CR の対象を、要件 ID の一覧(`target_requirement_ids`)から、要件 ID と変更の種類の組の一覧
(`targets`。`[{requirement_id, kind}]`)に変える。既存の行は、すべて「内容の変更」(`content`)に
写す。downgrade は要件 ID だけを戻す(種類は消える)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9f3e279a0210'
down_revision: Union[str, Sequence[str], None] = 'e1cfb47f1d13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

_change_requests = sa.table(
    'change_requests',
    sa.column('id', sa.Uuid()),
    sa.column('target_requirement_ids', _JSON),
    sa.column('targets', _JSON),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('change_requests', sa.Column('targets', _JSON, nullable=True))
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(_change_requests.c.id, _change_requests.c.target_requirement_ids)
    ).all()
    for row_id, requirement_ids in rows:
        targets = [{'requirement_id': r, 'kind': 'content'} for r in requirement_ids or []]
        conn.execute(
            _change_requests.update()
            .where(_change_requests.c.id == row_id)
            .values(targets=targets)
        )
    op.alter_column('change_requests', 'targets', existing_type=_JSON, nullable=False)
    op.drop_column('change_requests', 'target_requirement_ids')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'change_requests', sa.Column('target_requirement_ids', _JSON, nullable=True)
    )
    conn = op.get_bind()
    rows = conn.execute(sa.select(_change_requests.c.id, _change_requests.c.targets)).all()
    for row_id, targets in rows:
        requirement_ids = [t['requirement_id'] for t in targets or []]
        conn.execute(
            _change_requests.update()
            .where(_change_requests.c.id == row_id)
            .values(target_requirement_ids=requirement_ids)
        )
    op.alter_column(
        'change_requests', 'target_requirement_ids', existing_type=_JSON, nullable=False
    )
    op.drop_column('change_requests', 'targets')
