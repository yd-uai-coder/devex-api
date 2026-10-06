"""段階2 データフローの組み立て(処理概要表の下書きの統合)と検証のテスト。

SUT: merge_summaries / dfd_subject / group_functions(app/detailed_design/data_flow.py)、
     validate_data_flow / validate_stage / STAGE_VALIDATORS(app/detailed_design/validation.py)、
     パッケージの re-export(app/detailed_design/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。DFD の状態は DB から
読まず、`StageSources.dfd_diagrams`に要約を渡す(読み取りはサービス層の責務)。
"""

from tests.fixtures.detailed_design import data_flow_model, function_list_model

from app.detailed_design import (
    MAX_DFD_GROUPS,
    STAGE_VALIDATORS,
    DataFlowModel,
    DfdDiagramSummary,
    FunctionListModel,
    ProcessSummaryDraft,
    ProcessSummaryRow,
    StageSources,
    dfd_subject,
    group_functions,
    has_errors,
    merge_summaries,
    validate_stage,
)
from app.detailed_design.validation import validate_data_flow


def _function_list() -> FunctionListModel:
    return FunctionListModel.model_validate(
        {
            "groups": ["予約", "備品"],
            "functions": [
                {"id": "F-01", "name": "予約を登録する", "group": "予約"},
                {"id": "F-02", "name": "予約を取り消す", "group": "予約"},
                {"id": "F-03", "name": "備品を返す", "group": "備品"},
            ],
            "next_number": 4,
        }
    )


def _sources(**diagrams: DfdDiagramSummary) -> StageSources:
    return StageSources(
        stages={1: _function_list().model_dump()},
        dfd_diagrams={dfd_subject(k): v for k, v in diagrams.items()},
    )


def _codes(model: dict, sources: StageSources) -> list[str]:
    return [issue.code for issue in validate_data_flow(model, sources)]


def _row(function_id: str) -> dict:
    return {"function_id": function_id, "input": "a", "process": "b", "output": "c"}


def test_merge_then_validate_without_errors() -> None:
    """統合スモーク: 下書きを組み立てた結果が、段階2の検証をエラーなしで通る(fixture も)。"""
    function_list = FunctionListModel.model_validate(function_list_model())
    model = merge_summaries([ProcessSummaryDraft("F-01", "a", "b", "c")], function_list)

    issues = validate_stage(2, model.model_dump(), StageSources(stages={1: function_list_model()}))
    fixture_issues = validate_stage(
        2, data_flow_model(), StageSources(stages={1: function_list_model()})
    )

    assert STAGE_VALIDATORS[2] is validate_data_flow
    assert has_errors(issues) is False
    # fixture は DFD を描くグループを選んでいないので、その警告だけが出る
    assert [issue.code for issue in fixture_issues] == ["NO_DFD_GROUPS"]


def test_merge_summaries_orders_by_function_list_and_keeps_previous() -> None:
    previous = DataFlowModel(
        dfd_groups=["予約", "消えたグループ"],
        summaries=[ProcessSummaryRow(function_id="F-03", input="人が書いた入力")],
    )
    drafts = [
        ProcessSummaryDraft("F-02", " 取消 ", "消す", "なし"),
        ProcessSummaryDraft("F-01", "予約", "保存", "予約"),
        ProcessSummaryDraft("F-01", "二重", "二重", "二重"),
        ProcessSummaryDraft("F-99", "x", "x", "x"),
    ]

    model = merge_summaries(drafts, _function_list(), previous)

    assert [row.function_id for row in model.summaries] == ["F-01", "F-02", "F-03"]
    assert model.summaries[0].input == "予約"
    assert model.summaries[1].input == "取消"
    assert model.summaries[2].input == "人が書いた入力"
    assert model.dfd_groups == ["予約"]


def test_merge_summaries_adds_blank_row_for_undrafted_function() -> None:
    model = merge_summaries([], _function_list())

    assert [row.model_dump() for row in model.summaries][0] == {
        "function_id": "F-01",
        "input": "",
        "process": "",
        "output": "",
    }


def test_group_functions_keeps_order() -> None:
    assert [row.id for row in group_functions(_function_list(), "予約")] == ["F-01", "F-02"]
    assert dfd_subject(" 予約 ") == "予約"


def test_validate_summary_rows() -> None:
    model = {
        "dfd_groups": [],
        "summaries": [
            _row("F-01"),
            _row("F-01"),
            {"function_id": "F-02", "input": "", "process": "b", "output": "c"},
            _row("F-09"),
        ],
    }

    codes = _codes(model, _sources())

    assert codes.count("DUPLICATE_SUMMARY") == 1
    assert "EMPTY_SUMMARY" in codes
    assert "UNKNOWN_FUNCTION" in codes
    assert "MISSING_SUMMARY" in codes  # F-03


def test_validate_dfd_groups() -> None:
    summaries = [_row("F-01"), _row("F-02"), _row("F-03")]
    too_many = [f"g{i}" for i in range(MAX_DFD_GROUPS + 1)]

    assert "TOO_MANY_DFD_GROUPS" in _codes(
        {"dfd_groups": too_many, "summaries": summaries}, _sources()
    )
    codes = _codes({"dfd_groups": ["予約", "予約", "無い"], "summaries": summaries}, _sources())
    assert "DUPLICATE_DFD_GROUP" in codes
    assert "UNKNOWN_DFD_GROUP" in codes
    assert "DFD_MISSING" in codes


def test_validate_dfd_state_and_processes() -> None:
    summaries = [_row("F-01"), _row("F-02"), _row("F-03")]
    model = {"dfd_groups": ["予約"], "summaries": summaries}

    generating = _sources(予約=DfdDiagramSummary("draft", "generating"))
    draft = _sources(予約=DfdDiagramSummary("reviewing", "completed", ("F-01", "F-03")))
    approved = _sources(予約=DfdDiagramSummary("exported", "completed", ("F-01", "F-02")))

    assert _codes(model, generating) == ["DFD_GENERATING"]
    assert _codes(model, draft) == [
        "DFD_NOT_APPROVED",
        "DFD_FOREIGN_PROCESS",
        "DFD_MISSING_PROCESS",
    ]
    assert _codes(model, approved) == []


def test_validate_warns_when_no_dfd_group_is_selected() -> None:
    summaries = [_row("F-01"), _row("F-02"), _row("F-03")]

    issues = validate_data_flow({"dfd_groups": [], "summaries": summaries}, _sources())

    assert [issue.code for issue in issues] == ["NO_DFD_GROUPS"]
    assert has_errors(issues) is False


def test_validate_invalid_model() -> None:
    issues = validate_data_flow({"summaries": "x"}, _sources())

    assert [issue.code for issue in issues] == ["INVALID_MODEL"]
    assert has_errors(issues)
