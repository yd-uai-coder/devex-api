"""add stable unit keys to plan (stage 7) and procedure docs (stage 8)

Revision ID: d4e5f6a7b8c0
Revises: c3d4e5f6a7b9
Create Date: 2026-10-11 12:00:00.000000

段階7のタスクに、並べ替えで変わらない単位のキー(`key`。`U-001`…)を振り、段階8の手順書に
そのキー(`unit_key`)を書く(データだけの移行。列は変えない)。

- 段階7: キーの無いタスクに、並び順で`U-001`から振る。`next_unit_seq`は振った次の番号。
- 段階8: 手順書の`unit_id`(並び順から導いた`M-01-T01`)を、同じプロジェクトの段階7の今の
  並び順で引き、そのタスクのキーを`unit_key`に書く。段階7に無い ID の手順書はキーを書かない
  (検証の UNIT_MISMATCH のまま)。段階7の無いプロジェクト(簡易モード)は変えない(単位の ID を
  キーとして使う)。
- 内容の意味は変わらないので、状態と版は変えない(承認済みのまま。陳腐化も起きない)。

変換は、このファイルの中の純粋関数で行い、app のコードを import しない(後の改修で、この移行の
結果が変わらないようにするため)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c0'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLAN_STAGE = 7
PROCEDURE_DOC_STAGE = 8

_json = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
_stages = sa.table(
    'design_stages',
    sa.column('id', sa.Uuid()),
    sa.column('project_id', sa.Uuid()),
    sa.column('stage', sa.SmallInteger()),
    sa.column('model', _json),
)


def _key_seq(key: str) -> int:
    head, _, number = key.partition('-')
    return int(number) if head == 'U' and number.isdigit() else 0


def key_plan(model: dict) -> tuple[dict, dict[str, str]]:
    """段階7の model のタスクにキーを振る。戻り値は新しい model と、単位の ID → キーの対応。

    キーのあるタスクはそのまま(何度流しても同じ結果)。単位の ID は並び順から
    `M-<マイルストーン2桁>-T<タスク2桁>`(app の`task_id`と同じ規則)。"""
    milestones = model.get('milestones') or []
    tasks = [t for m in milestones for t in m.get('tasks') or []]
    used = [str(t.get('key') or '') for t in tasks]
    seq = max([int(model.get('next_unit_seq') or 1), *(_key_seq(k) + 1 for k in used if k)])
    seen: set[str] = set()
    ids: dict[str, str] = {}
    new_milestones = []
    for mi, milestone in enumerate(milestones):
        new_tasks = []
        for ti, task in enumerate(milestone.get('tasks') or []):
            key = str(task.get('key') or '')
            if not key or key in seen:
                key, seq = f'U-{seq:03d}', seq + 1
            seen.add(key)
            ids[f'M-{mi + 1:02d}-T{ti + 1:02d}'] = key
            new_tasks.append({**task, 'key': key})
        new_milestones.append({**milestone, 'tasks': new_tasks})
    return {**model, 'milestones': new_milestones, 'next_unit_seq': seq}, ids


def key_units(model: dict, ids: dict[str, str]) -> dict:
    """段階8の手順書に、単位の ID で引いたキーを書く(引けないもの・キーのあるものはそのまま)。"""
    units = []
    for unit in model.get('units') or []:
        uid = str(unit.get('unit_id') or '').strip()
        if not unit.get('unit_key') and uid in ids:
            unit = {**unit, 'unit_key': ids[uid]}
        units.append(unit)
    return {**model, 'units': units}


def unkey_plan(model: dict) -> dict:
    """段階7の model からキーを消す(downgrade)。"""
    milestones = [
        {**m, 'tasks': [{k: v for k, v in t.items() if k != 'key'} for t in m.get('tasks') or []]}
        for m in model.get('milestones') or []
    ]
    rest = {k: v for k, v in model.items() if k != 'next_unit_seq'}
    return {**rest, 'milestones': milestones}


def unkey_units(model: dict) -> dict:
    """段階8の手順書からキーを消す(downgrade)。"""
    units = [{k: v for k, v in u.items() if k != 'unit_key'} for u in model.get('units') or []]
    return {**model, 'units': units}


def _rows(bind, stage: int):
    return bind.execute(
        sa.select(_stages.c.id, _stages.c.project_id, _stages.c.model).where(
            _stages.c.stage == stage
        )
    ).all()


def _update(bind, row_id, model: dict) -> None:
    bind.execute(_stages.update().where(_stages.c.id == row_id).values(model=model))


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    ids_by_project: dict = {}
    for row in _rows(bind, PLAN_STAGE):
        if not row.model:
            continue
        model, ids = key_plan(row.model)
        ids_by_project[row.project_id] = ids
        _update(bind, row.id, model)
    for row in _rows(bind, PROCEDURE_DOC_STAGE):
        ids = ids_by_project.get(row.project_id)
        if not row.model or ids is None:
            continue
        _update(bind, row.id, key_units(row.model, ids))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    for row in _rows(bind, PLAN_STAGE):
        if row.model:
            _update(bind, row.id, unkey_plan(row.model))
    for row in _rows(bind, PROCEDURE_DOC_STAGE):
        if row.model:
            _update(bind, row.id, unkey_units(row.model))
