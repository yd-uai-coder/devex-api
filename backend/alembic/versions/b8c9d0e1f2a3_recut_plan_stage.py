"""recut plan stage (stage 7) tasks into units

Revision ID: b8c9d0e1f2a3
Revises: a5b6c7d8e9f0
Create Date: 2026-10-07 12:00:00.000000

段階7のタスクを、区分(area)の横割りから作業単位の形に変える(データだけの移行。列は変えない)。

- 処理のあるタスクは機能(feature)、処理の無いタスクは基盤(base)にする。区分は捨てる。
- ファイル(modules)は、同じプロジェクトの段階4のモジュール一覧のパスに一致するものをモジュール、
  残り(Dockerfile などの環境・設定のファイル)を config_files に分ける。
- 依存(depends_on)は空にする(人か AI の再生成で入れる)。
- マイルストーンの処理(function_ids)は捨てる(タスクの処理から導くため)。タスクに無い処理は、
  検証の「どの単位にもない処理」で見える。
- 承認済みの段階7はレビュー中に戻す(新しい形の検証を通して承認し直してもらうため)。内容が
  変わるので、どの行も版を1つ上げる(開いている画面の保存は版の不一致で止まる)。

変換は、このファイルの中の純粋関数で行い、app のコードを import しない(後の改修で、この移行の
結果が変わらないようにするため)。
"""
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a5b6c7d8e9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLAN_STAGE = 7
STRUCTURE_STAGE = 4

_json = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
_stages = sa.table(
    'design_stages',
    sa.column('id', sa.Uuid()),
    sa.column('project_id', sa.Uuid()),
    sa.column('stage', sa.SmallInteger()),
    sa.column('status', sa.String(20)),
    sa.column('model', _json),
    sa.column('version', sa.Integer()),
)


def _strings(value: Any) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def is_recut(model: dict) -> bool:
    """新しい形(タスクに kind がある)か。タスクが1つも無ければ、どちらの形でも同じとみなす。"""
    tasks = [t for m in model.get('milestones') or [] for t in m.get('tasks') or []]
    return any('kind' in t for t in tasks)


def recut_plan(model: dict, module_paths: Sequence[str]) -> dict:
    """改修前の段階7の model を、作業単位の形に変える(model 以外の値はそのまま残す)。"""
    paths = {p.strip() for p in module_paths}
    milestones = []
    for milestone in model.get('milestones') or []:
        tasks = []
        for task in milestone.get('tasks') or []:
            function_ids = _strings(task.get('function_ids'))
            files = _strings(task.get('modules'))
            tasks.append(
                {
                    'kind': 'feature' if function_ids else 'base',
                    'title': str(task.get('title') or ''),
                    'function_ids': function_ids,
                    'depends_on': [],
                    'modules': [f for f in files if f in paths],
                    'config_files': [f for f in files if f not in paths],
                }
            )
        rest = {k: v for k, v in milestone.items() if k not in ('function_ids', 'tasks')}
        milestones.append({**rest, 'tasks': tasks})
    return {**model, 'milestones': milestones}


def restore_plan(model: dict) -> dict:
    """作業単位の形を、改修前の形に戻す(downgrade。機能はバックエンド、基盤は準備の区分にし、
    2つのファイルの欄をまとめる。マイルストーンの処理はタスクの処理から作る)。"""
    milestones = []
    for milestone in model.get('milestones') or []:
        tasks = []
        for task in milestone.get('tasks') or []:
            tasks.append(
                {
                    'area': 'バックエンド' if task.get('kind') == 'feature' else '準備',
                    'title': str(task.get('title') or ''),
                    'modules': _strings(task.get('modules')) + _strings(task.get('config_files')),
                    'function_ids': _strings(task.get('function_ids')),
                }
            )
        function_ids = list(dict.fromkeys(f for t in tasks for f in t['function_ids']))
        rest = {k: v for k, v in milestone.items() if k != 'tasks'}
        milestones.append({**rest, 'function_ids': function_ids, 'tasks': tasks})
    return {**model, 'milestones': milestones}


def _module_paths(model: dict | None) -> list[str]:
    modules = (model or {}).get('modules') or []
    return [str(m.get('path') or '') for m in modules if isinstance(m, dict)]


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    structures = {
        row.project_id: row.model
        for row in bind.execute(
            sa.select(_stages.c.project_id, _stages.c.model).where(
                _stages.c.stage == STRUCTURE_STAGE
            )
        )
    }
    rows = bind.execute(
        sa.select(_stages.c.id, _stages.c.project_id, _stages.c.status, _stages.c.model,
                  _stages.c.version).where(_stages.c.stage == PLAN_STAGE)
    ).all()
    for row in rows:
        if not row.model or is_recut(row.model):
            continue
        model = recut_plan(row.model, _module_paths(structures.get(row.project_id)))
        status = 'reviewing' if row.status == 'approved' else row.status
        bind.execute(
            _stages.update()
            .where(_stages.c.id == row.id)
            .values(model=model, status=status, version=row.version + 1)
        )


def downgrade() -> None:
    """Downgrade schema."""
    # 形だけを戻す。upgrade で戻した承認は戻さない(承認し直してもらう)
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_stages.c.id, _stages.c.model, _stages.c.version).where(
            _stages.c.stage == PLAN_STAGE
        )
    ).all()
    for row in rows:
        if not row.model or not is_recut(row.model):
            continue
        bind.execute(
            _stages.update()
            .where(_stages.c.id == row.id)
            .values(model=restore_plan(row.model), version=row.version + 1)
        )
