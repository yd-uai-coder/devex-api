"""段階7のデータ移行(区分の横割り → 作業単位)の変換のテスト。

SUT: is_recut / recut_plan / restore_plan
     (alembic/versions/b8c9d0e1f2a3_recut_plan_stage.py)
ドライバ: 各テスト関数(移行ファイルを importlib で読み込み、純粋関数を直接呼ぶ)
スタブ不要 ── 変換は純粋関数(副作用なし)で、DB を呼ばないため(行の読み書きは upgrade・downgrade
の責務で、ここでは扱わない)。変換の結果が新しい形の検証を通ることは、app の`validate_plan`で
確かめる(移行ファイル自身は app を import しない)。
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from tests.fixtures.detailed_design import function_list_model, module_list_model

from app.detailed_design import PlanModel, StageSources
from app.detailed_design.validation import validate_plan

ROUTE = "app/api/routes/reservations.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_PATH = _VERSIONS / "b8c9d0e1f2a3_recut_plan_stage.py"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("recut_plan_stage", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_model() -> dict:
    """改修前の形の段階7(区分ごとのタスク・マイルストーンの処理・例のファイル)。"""
    return {
        "crosscutting": [{"topic": "認証", "policy": "JWT", "modules": ["Dockerfile"]}],
        "milestones": [
            {
                "name": "予約の登録",
                "goal": "予約を登録できる",
                "priority": "Must",
                "function_ids": ["F-01"],
                "tasks": [
                    {
                        "area": "準備",
                        "title": "環境を作る",
                        "modules": [" Dockerfile ", ""],
                        "function_ids": [],
                    },
                    {
                        "area": "バックエンド",
                        "title": "予約の API",
                        "modules": [ROUTE, "docker-compose.yml"],
                        "function_ids": ["F-01"],
                    },
                ],
            }
        ],
        "environment": "uv",
        "risks": [{"risk": "遅延", "mitigation": "削る"}],
    }


def test_smoke_recut_old_model_passes_new_validation():
    migration = _migration()
    assert migration.down_revision == "a5b6c7d8e9f0"
    old = _old_model()
    assert not migration.is_recut(old)
    model = migration.recut_plan(old, [ROUTE])
    assert migration.is_recut(model)
    sources = StageSources(stages={1: function_list_model(), 4: module_list_model()})
    # 単位の指摘は無い(既定の横断事項が足りない警告だけ)
    assert [i.code for i in validate_plan(model, sources)] == ["MISSING_TOPIC"] * 3


def test_recut_splits_files_by_module_paths_and_sets_kind():
    model = _migration().recut_plan(_old_model(), [ROUTE])
    milestone = model["milestones"][0]
    assert "function_ids" not in milestone
    assert (milestone["name"], milestone["priority"]) == ("予約の登録", "Must")
    assert milestone["tasks"] == [
        {
            "kind": "base",
            "title": "環境を作る",
            "function_ids": [],
            "depends_on": [],
            "modules": [],
            "config_files": ["Dockerfile"],
        },
        {
            "kind": "feature",
            "title": "予約の API",
            "function_ids": ["F-01"],
            "depends_on": [],
            "modules": [ROUTE],
            "config_files": ["docker-compose.yml"],
        },
    ]
    # 横断事項・開発環境・リスクはそのまま
    old = _old_model()
    assert (model["crosscutting"], model["environment"], model["risks"]) == (
        old["crosscutting"],
        old["environment"],
        old["risks"],
    )
    PlanModel.model_validate(model)


def test_recut_without_structure_puts_all_files_into_config_files():
    model = _migration().recut_plan(_old_model(), [])
    task = model["milestones"][0]["tasks"][1]
    assert (task["modules"], task["config_files"]) == ([], [ROUTE, "docker-compose.yml"])


def test_is_recut_treats_models_without_tasks_as_old():
    migration = _migration()
    assert not migration.is_recut({})
    assert not migration.is_recut({"milestones": [{"name": "a", "tasks": []}]})
    assert migration.is_recut({"milestones": [{"name": "a", "tasks": [{"kind": "base"}]}]})


def test_restore_plan_returns_the_old_shape():
    migration = _migration()
    restored = migration.restore_plan(migration.recut_plan(_old_model(), [ROUTE]))
    assert not migration.is_recut(restored)
    milestone = restored["milestones"][0]
    assert milestone["function_ids"] == ["F-01"]
    assert milestone["tasks"] == [
        {"area": "準備", "title": "環境を作る", "modules": ["Dockerfile"], "function_ids": []},
        {
            "area": "バックエンド",
            "title": "予約の API",
            "modules": [ROUTE, "docker-compose.yml"],
            "function_ids": ["F-01"],
        },
    ]
