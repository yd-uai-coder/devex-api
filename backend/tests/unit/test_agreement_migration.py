"""既存の束からの合意の記録の移行(データのマイグレーション)の変換のテスト。

SUT: endpoint_key / agreed_items / baseline_records
     (alembic/versions/75d414ded7cb_backfill_agreement_records.py)
ドライバ: 各テスト関数(移行ファイルを importlib で読み込み、純粋関数を直接呼ぶ)
スタブ不要 ── 変換は純粋関数(副作用なし)で、DB を呼ばないため(行の読み書きは upgrade の責務で、
ここでは扱わない)。キーと中身が app の規則と同じことは、app の`agreement_items`・`body_of`と
突き合わせて確かめる(移行ファイル自身は app を import しない)。
"""

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

from tests.fixtures.change_impact import impact_documents

from app.agreements import agreement_items, body_of
from app.detailed_design.api_list import endpoint_key
from app.documents.external_design.model import ExternalDesignModel
from app.documents.requirements.model import RequirementsModel

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_PATH = _VERSIONS / "75d414ded7cb_backfill_agreement_records.py"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("backfill_agreement_records", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _documents() -> dict[str, dict]:
    documents = impact_documents()
    documents["requirements"]["users"] = [{"user": "利用者", "scene": "予約する", "source": "user"}]
    documents["external_design"]["apis"].append(
        {"method": "delete", "path": "`/api/v1/reservations/{reservation_id}/`"}
    )
    documents["external_design"]["integrations"] = [
        {"service": " メール送信 ", "purpose": "確認メール"},
        {"service": "", "purpose": "名前が空(捨てる)"},
    ]
    return documents


def test_endpoint_key_matches_app() -> None:
    migration = _migration()
    for method, path in [("get", "/x/"), ("POST", "`/a/{id}/b`"), ("put", "")]:
        assert migration.endpoint_key(method, path) == endpoint_key(method, path)


def test_items_match_app_keys_labels_and_bodies() -> None:
    documents = _documents()
    migrated = _migration().agreed_items(documents["requirements"], documents["external_design"])
    expected = agreement_items(
        RequirementsModel.model_validate(documents["requirements"]),
        ExternalDesignModel.model_validate(documents["external_design"]),
    )
    assert [(k, kind, label) for k, kind, label, _ in migrated] == [
        (i.key, i.kind, i.label) for i in expected
    ]
    for (_, kind, _, content), item in zip(migrated, expected, strict=True):
        assert body_of(kind, content) == body_of(item.kind, item.content)
    assert _migration().agreed_items(None, None) == []


def test_baseline_records_follow_baseline_order_and_removals() -> None:
    """B-1 → B-2(FR-002 を外し、FR-001 を変えた)→ B-3(FR-002 が戻った)。"""
    migration = _migration()
    first = _documents()
    second = _documents()
    rows = second["requirements"]["rows"]
    second["requirements"]["rows"] = [{**rows[0], "text": "変えた"}, rows[2]]
    ids = [uuid.uuid4() for _ in range(3)]
    user = uuid.uuid4()
    baselines = [
        {"id": ids[0], "recorded_by": user, "created_at": "t1", **first},
        {"id": ids[1], "recorded_by": None, "created_at": "t2", **second},
        {"id": ids[2], "recorded_by": None, "created_at": "t3", **first},
    ]

    records = migration.baseline_records(baselines)

    per_baseline = len(migration.agreed_items(first["requirements"], first["external_design"]))
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    b2 = [r for r in records if r["baseline_id"] == ids[1]]
    assert [r["key"] for r in b2 if r["content"] is None] == ["req:FR-002"]
    removed = next(r for r in b2 if r["content"] is None)
    assert (removed["kind"], removed["label"]) == ("requirement", "FR-002 予約の一覧を見られる")
    assert next(r for r in b2 if r["key"] == "req:FR-001")["content"]["text"] == "変えた"
    b3 = [r for r in records if r["baseline_id"] == ids[2]]
    assert len(b3) == per_baseline and all(r["content"] is not None for r in b3)
    assert {r["recorded_by"] for r in records if r["baseline_id"] == ids[0]} == {user}
    assert {r["created_at"] for r in b3} == {"t3"}


def test_baseline_without_documents_removes_everything() -> None:
    """写しの無い束(文書が入っていない)は、それまでの合意をすべて外す。"""
    migration = _migration()
    documents = _documents()
    baselines = [
        {"id": uuid.uuid4(), **documents},
        {"id": uuid.uuid4()},
    ]
    records = migration.baseline_records(baselines)
    second = [r for r in records if r["baseline_id"] == baselines[1]["id"]]
    assert second and all(r["content"] is None for r in second)
