"""段階7・8 のデータ移行(作業単位の安定キー)の変換のテスト。

SUT: key_plan / key_units / unkey_plan / unkey_units
     (alembic/versions/d4e5f6a7b8c0_add_unit_keys.py)
ドライバ: 各テスト関数(移行ファイルを importlib で読み込み、純粋関数を直接呼ぶ)
スタブ不要 ── 変換は純粋関数(副作用なし)で、DB を呼ばないため(行の読み書きは upgrade・downgrade
の責務で、ここでは扱わない)。変換の結果を app の model が読め、手順書がキーで単位に付くことは、
app の`relabel_units`で確かめる(移行ファイル自身は app を import しない)。
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from tests.fixtures.detailed_design import plan_model, procedure_doc_model

from app.detailed_design import PlanModel, ProcedureDocModel, plan_units, relabel_units

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_PATH = _VERSIONS / "d4e5f6a7b8c0_add_unit_keys.py"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("add_unit_keys", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_plan() -> dict:
    """キーの無い段階7(移行前の形)。2つ目のマイルストーンにタスクを1つ足す。"""
    model = plan_model()
    for task in model["milestones"][0]["tasks"]:
        del task["key"]
    del model["next_unit_seq"]
    model["milestones"].append(
        {"name": "公開", "tasks": [{"kind": "base", "title": "デプロイする"}]}
    )
    return model


def test_key_plan_numbers_tasks_in_order_and_maps_unit_ids() -> None:
    migration = _migration()

    model, ids = migration.key_plan(_old_plan())

    plan = PlanModel.model_validate(model)
    assert [u.key for u in plan_units(plan)] == ["U-001", "U-002", "U-003"]
    assert plan.next_unit_seq == 4
    assert ids == {"M-01-T01": "U-001", "M-01-T02": "U-002", "M-02-T01": "U-003"}


def test_key_plan_keeps_existing_keys() -> None:
    """何度流しても同じ結果(キーのあるタスクはそのまま)。"""
    migration = _migration()
    once, _ = migration.key_plan(_old_plan())

    twice, ids = migration.key_plan(once)

    assert twice == once
    assert ids["M-02-T01"] == "U-003"


def test_key_units_writes_keys_found_by_unit_id() -> None:
    migration = _migration()
    doc = procedure_doc_model(unit_key="")
    doc["units"].append({"unit_id": "M-09-T01", "title": "無い単位"})
    plan, ids = migration.key_plan(_old_plan())

    keyed = migration.key_units(doc, ids)

    assert [u.get("unit_key") for u in keyed["units"]] == ["U-002", None]
    # 並べ替えた後も、移行で書いたキーで同じ単位に付く
    reordered = PlanModel.model_validate(plan)
    reordered.milestones[0].tasks.reverse()
    relabeled = relabel_units(ProcedureDocModel.model_validate(keyed), reordered)
    assert relabeled.units[0].unit_id == "M-01-T01"


def test_downgrade_removes_keys() -> None:
    migration = _migration()
    plan, ids = migration.key_plan(_old_plan())
    doc = migration.key_units(procedure_doc_model(unit_key=""), ids)

    assert migration.unkey_plan(plan) == _old_plan()
    assert "unit_key" not in migration.unkey_units(doc)["units"][0]
