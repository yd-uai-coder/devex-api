"""変更要求(CR)に結ぶ影響の予測のテスト。

SUT: app.change_requests の baseline_models / requirement_texts / predict / predict_from_snapshots
     と、Prediction の保存の形(to_dict / from_dict)、app.detailed_design の ER_SNAPSHOT_KEY
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため。入力は、要件 FR-001・FR-002・
NFR-001(FR-001 と FR-002 は画面 SCR-001 を共有)と段階1〜8 を持つ fixture の model を、束の写しの
形(key → model)に並べたもの
"""

from tests.fixtures.change_impact import impact_documents, impact_stages

from app.change_requests import (
    Prediction,
    Target,
    baseline_models,
    predict,
    predict_from_snapshots,
    requirement_texts,
)
from app.detailed_design import ER_SNAPSHOT_KEY
from app.detailed_design.function_list import FunctionListModel
from app.detailed_design.impact import Change, build_elements, impact_of


def _snapshots() -> dict[str, dict]:
    stages = {f"stage:{n}": model for n, model in impact_stages().items()}
    return {**impact_documents(), **stages}


def test_single_target_matches_impact_of() -> None:
    """1つの対象の予測は、今のたどり方(impact_of)と同じ要素・道・理由になる(画面・API も入る)。"""
    snapshots = _snapshots()
    elements = build_elements(baseline_models(snapshots))

    prediction = predict_from_snapshots(snapshots, [Target("FR-001")], adds_requirement=False)

    expected = impact_of(elements, Change("req:FR-001"))
    assert [(i.key, i.path, i.reason) for i in prediction.items] == [
        (i.key, i.path, i.reason) for i in expected
    ]
    assert {"screen:SCR-001", "api:POST /api/v1/reservations", "unit:U-002"} <= {
        i.key for i in prediction.items
    }
    assert [(t.requirement_id, t.kind, t.text) for t in prediction.targets] == [
        ("FR-001", "content", "予約を登録できる")
    ]
    assert prediction.unpredicted_new_requirement is False


def test_targets_are_merged_with_reached_requirements() -> None:
    """複数の対象は1つの一覧にまとまり、共有の要素には届いた対象がすべて残る(短い道・先の対象)。"""
    prediction = predict_from_snapshots(
        _snapshots(), [Target("FR-001"), Target("FR-002")], adds_requirement=False
    )

    by_key = {i.key: i for i in prediction.items}
    assert by_key["screen:SCR-001"].requirement_ids == ("FR-001", "FR-002")
    assert by_key["screen:SCR-001"].path == ("req:FR-001", "screen:SCR-001")
    assert by_key["unit:U-003"].requirement_ids == ("FR-002",)
    assert [i.stage for i in prediction.items] == sorted(i.stage for i in prediction.items)
    assert not any(i.key.startswith("req:") for i in prediction.items)


def test_delete_kind_traces_like_content() -> None:
    """削除の対象も、内容の変更と同じ範囲をたどる(種類は対象に残る)。"""
    snapshots = _snapshots()

    content = predict_from_snapshots(snapshots, [Target("FR-002")], adds_requirement=False)
    delete = predict_from_snapshots(
        snapshots, [Target("FR-002", "delete")], adds_requirement=False
    )

    assert delete.items == content.items
    assert delete.targets[0].kind == "delete"


def test_new_requirement_only_has_no_items() -> None:
    prediction = predict_from_snapshots(_snapshots(), [], adds_requirement=True)

    assert (prediction.targets, prediction.items) == ((), ())
    assert prediction.unpredicted_new_requirement is True


def test_baseline_models_reads_stages_and_er_snapshot() -> None:
    snapshots = _snapshots()
    er = {"reservations": {"columns": [{"name": "id"}], "description": "予約"}}

    without_er = baseline_models(snapshots)
    stage3 = {**snapshots["stage:3"], ER_SNAPSHOT_KEY: er}
    with_er = baseline_models({**snapshots, "stage:3": stage3})
    no_stages = baseline_models({"requirements": snapshots["requirements"]})

    assert without_er.er_tables == ()
    assert with_er.er_tables == ("reservations",)
    assert with_er.functions == without_er.functions
    assert no_stages.functions == FunctionListModel() and no_stages.requirements is not None


def test_requirement_texts_and_missing_target_text() -> None:
    texts = requirement_texts(impact_documents()["requirements"])
    assert texts["NFR-001"] == "本人の予約だけを見られる"
    assert requirement_texts(None) == {}
    elements = build_elements(baseline_models(_snapshots()))

    prediction = predict(elements, [Target("FR-404")], adds_requirement=False)

    assert prediction.items == ()
    assert prediction.targets[0].text == ""


def test_prediction_round_trips_through_saved_form() -> None:
    prediction = predict_from_snapshots(
        _snapshots(), [Target("FR-001"), Target("FR-002", "delete")], adds_requirement=True
    )

    saved = prediction.to_dict()

    assert Prediction.from_dict(saved) == prediction
    assert saved["items"][0]["path"][0].startswith("req:")
