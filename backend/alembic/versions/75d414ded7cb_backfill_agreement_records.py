"""backfill agreement records from existing baselines

Revision ID: 75d414ded7cb
Revises: 05c8f8d7cf8b
Create Date: 2026-10-20 12:30:00.000000

既存の合意済みの版(束。ステージ11 で作ったもの)から、要素ごとの合意の記録を作る(データだけの
移行。列は変えない)。

- プロジェクトごとに、束を番号の順に読み、束を作ったときと同じ規則で記録する。束の要件定義書・
  外部設計書の写しの要素(要件・利用者と使う場面・画面・API・外部連携)をまとめて合意にし、その
  時点の合意にあって束に無い要素を「外れた」(中身が NULL)と記録する。
- 記録した日時・記録した人は束のもの(`created_at`・`recorded_by`)。出どころは束。
- もう合意の記録があるプロジェクトは変えない(何度流しても同じ結果)。
- 「採用にする」の過去の操作は戻せない(誰が・いつを残していなかったため)。束に入った行は、束の
  合意として記録される。
- downgrade は何もしない。記録の表は前のマイグレーション(`05c8f8d7cf8b`)の downgrade で消える。
  移行の後に作った記録と見分けられないので、移行の分だけを消すことはしない。

変換は、このファイルの中の純粋関数で行い、app のコードを import しない(後の改修で、この移行の
結果が変わらないようにするため)。キーと中身は app の`app.agreements.agreement_items`と同じ
(テストで突き合わせる)。中身は写しの行をそのまま写す(既定値で埋めない。比べるときは app の
`body_of`が既定値をそろえる)。
"""
import re
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '75d414ded7cb'
down_revision: Union[str, Sequence[str], None] = '05c8f8d7cf8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

USERS_KEY = 'users'
USERS_LABEL = '利用者と使う場面'
_PATH_PARAM = re.compile(r'\{[^}]*\}')

_json = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
_baselines = sa.table(
    'baselines',
    sa.column('id', sa.Uuid()),
    sa.column('project_id', sa.Uuid()),
    sa.column('number', sa.Integer()),
    sa.column('recorded_by', sa.Uuid()),
    sa.column('created_at', sa.DateTime(timezone=True)),
)
_items = sa.table(
    'baseline_items',
    sa.column('baseline_id', sa.Uuid()),
    sa.column('key', sa.String()),
    sa.column('included', sa.Boolean()),
    sa.column('model', _json),
)
_records = sa.table(
    'agreement_records',
    sa.column('id', sa.Uuid()),
    sa.column('project_id', sa.Uuid()),
    sa.column('seq', sa.Integer()),
    sa.column('key', sa.String()),
    sa.column('kind', sa.String()),
    sa.column('label', sa.Text()),
    sa.column('content', _json),
    sa.column('origin', sa.String()),
    sa.column('baseline_id', sa.Uuid()),
    sa.column('change_request_id', sa.Uuid()),
    sa.column('recorded_by', sa.Uuid()),
    sa.column('created_at', sa.DateTime(timezone=True)),
)


def endpoint_key(method: str, path: str) -> str:
    """API のキー(app の`endpoint_key`と同じ規則)。"""
    path = path.strip().strip('`').rstrip('/') or '/'
    return f'{method.strip().upper()} {_PATH_PARAM.sub("{}", path)}'


def agreed_items(requirements: dict | None, external_design: dict | None) -> list[tuple[str, str, str, dict]]:
    """写しの model から、合意の対象の要素(キー・種類・表示名・中身)を文書の並び順に作る。
    同じキーは最初のものを残す。ID・サービス名の無い行と、空の利用者は要素にしない。"""
    found: dict[str, tuple[str, str, str, dict]] = {}

    def add(key: str, kind: str, label: str, content: dict) -> None:
        if key not in found:
            found[key] = (key, kind, label, content)

    if requirements:
        for row in requirements.get('rows') or []:
            if row.get('id'):
                add(f"req:{row['id']}", 'requirement', f"{row['id']} {row.get('text', '')}", row)
        users = requirements.get('users') or []
        if users:
            add(USERS_KEY, 'users', USERS_LABEL, {'users': users})
    if external_design:
        for screen in external_design.get('screens') or []:
            if screen.get('id'):
                add(f"screen:{screen['id']}", 'screen', f"{screen['id']} {screen.get('name', '')}", screen)
        for api in external_design.get('apis') or []:
            method, path = api.get('method') or '', api.get('path') or ''
            if method and path:
                add(f'api:{endpoint_key(method, path)}', 'api', f'{method} {path}', api)
        for integration in external_design.get('integrations') or []:
            service = (integration.get('service') or '').strip()
            if service:
                add(f'integration:{service}', 'integration', service, integration)
    return list(found.values())


def baseline_records(baselines: list[dict]) -> list[dict]:
    """1つのプロジェクトの束(番号の順。`id`・`recorded_by`・`created_at`・`requirements`・
    `external_design`を持つ dict)から、追記する記録の行を作る(`seq`は1から)。"""
    agreed: dict[str, tuple[str, str]] = {}  # キー → (種類, 表示名)。今の合意
    records: list[dict] = []
    for baseline in baselines:
        items = agreed_items(baseline.get('requirements'), baseline.get('external_design'))
        present = {key for key, _, _, _ in items}
        rows = [(key, kind, label, content) for key, kind, label, content in items]
        rows += [(key, kind, label, None) for key, (kind, label) in agreed.items() if key not in present]
        for key, kind, label, content in rows:
            records.append({
                'seq': len(records) + 1,
                'key': key,
                'kind': kind,
                'label': label,
                'content': content,
                'baseline_id': baseline['id'],
                'recorded_by': baseline.get('recorded_by'),
                'created_at': baseline.get('created_at'),
            })
            if content is None:
                agreed.pop(key, None)
            else:
                agreed[key] = (kind, label)
    return records


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    done = {row.project_id for row in bind.execute(sa.select(_records.c.project_id).distinct())}
    by_project: dict = {}
    for row in bind.execute(sa.select(_baselines).order_by(_baselines.c.project_id, _baselines.c.number)):
        if row.project_id in done:
            continue
        by_project.setdefault(row.project_id, []).append(
            {'id': row.id, 'recorded_by': row.recorded_by, 'created_at': row.created_at}
        )
    for project_id, baselines in by_project.items():
        for baseline in baselines:
            snapshots = bind.execute(
                sa.select(_items.c.key, _items.c.model).where(
                    _items.c.baseline_id == baseline['id'],
                    _items.c.included.is_(True),
                    _items.c.key.in_(('requirements', 'external_design')),
                )
            ).all()
            for snapshot in snapshots:
                baseline[snapshot.key] = snapshot.model
        records = baseline_records(baselines)
        if records:
            bind.execute(
                _records.insert(),
                [
                    {**record, 'id': uuid.uuid4(), 'project_id': project_id, 'origin': 'baseline', 'change_request_id': None}
                    for record in records
                ],
            )


def downgrade() -> None:
    """Downgrade schema."""
