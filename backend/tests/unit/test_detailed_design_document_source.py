"""詳細設計書の組み立ての入力と章の状態のテスト。

SUT: chapter_status / chapter_statuses / document_source / CHAPTERS / DocumentSource.status
     (app/detailed_design/document/source.py)、
     パッケージの re-export(app/detailed_design/document/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。段階の状態と内容は DB から読まず
dict で渡す(読み取りはサービス層の責務)。
"""

from tests.fixtures.detailed_design import ALL_APPROVED, document_stage_models

from app.detailed_design import FunctionListModel, LogicModel
from app.detailed_design.document import (
    CHAPTERS,
    DocumentSource,
    chapter_status,
    chapter_statuses,
    document_source,
)
from app.detailed_design.stages import StageState


def test_chapters_are_01_to_06_mapped_to_stages_1_to_6() -> None:
    assert [(c.stage, c.number) for c in CHAPTERS] == [
        (1, "01"),
        (2, "02"),
        (3, "03"),
        (4, "04"),
        (5, "05"),
        (6, "06"),
    ]


def test_chapter_status_is_unapproved_unless_stage_is_approved() -> None:
    for state in ("not_started", "draft", "regenerated", "reviewing", "outdated"):
        assert chapter_status(1, state, {"functions": []}) == "unapproved"
    assert chapter_status(1, "approved", {"functions": []}) == "approved"


def test_stage6_approved_with_no_logics_is_skipped() -> None:
    assert chapter_status(6, "approved", {"logics": []}) == "skipped"
    assert chapter_status(6, "approved", document_stage_models()[6]) == "approved"
    # 段階6以外は0件でも省略にしない(承認の条件で空の内容は通らない)
    assert chapter_status(5, "approved", {"procedures": []}) == "approved"


def test_chapter_statuses_treat_missing_stage_as_unapproved() -> None:
    statuses = chapter_statuses({1: "approved", 2: "outdated"}, {1: document_stage_models()[1]})

    assert statuses == {
        1: "approved",
        2: "unapproved",
        3: "unapproved",
        4: "unapproved",
        5: "unapproved",
        6: "unapproved",
    }


def test_document_source_parses_only_approved_models() -> None:
    models = document_stage_models()
    states: dict[int, StageState] = {**ALL_APPROVED, 3: "reviewing"}

    source = document_source("予約システム", states, models)

    assert isinstance(source, DocumentSource)
    assert source.title == "予約システム"
    assert isinstance(source.function_list, FunctionListModel)
    assert source.function_list.functions[0].id == "F-01"
    assert source.crud is None  # 段階3はレビュー中なので内容を渡さない
    assert source.status(3) == "unapproved"
    assert source.status(1) == "approved"


def test_document_source_passes_skipped_stage6_as_empty_model() -> None:
    models = {**document_stage_models(), 6: {"logics": []}}

    source = document_source("p", ALL_APPROVED, models)

    assert source.status(6) == "skipped"
    assert source.logics == LogicModel(logics=[])
