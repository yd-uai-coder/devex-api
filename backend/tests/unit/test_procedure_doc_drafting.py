"""段階8の下書きの入出力(プロンプト・出力スキーマ・手順書への変換)のテスト。

SUT: build_procedure_doc_messages / to_unit_procedure
     (app/detailed_design/procedure_doc_drafting.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、LLM を呼ばないため(メッセージを組み立て、
構造化出力を受け取って変換するだけ。LLM の呼び出しはサービス層の責務)。
"""

from tests.fixtures.detailed_design import document_stage_models, procedure_doc_output

from app.detailed_design import PlanModel, find_unit
from app.detailed_design.procedure_doc_drafting import (
    PROCEDURE_DOC_SYSTEM_PROMPT,
    GeneratedFinding,
    GeneratedUnitFile,
    ProcedureDocGenerationOutput,
    build_procedure_doc_messages,
    to_unit_procedure,
)
from app.detailed_design.procedure_doc_refs import unit_context
from app.detailed_design.prompt_rules import NAMING_RULES


def _unit(stages: dict[int, dict], unit_id: str = "M-01-T02"):
    unit = find_unit(PlanModel.model_validate(stages[7]), unit_id)
    assert unit is not None
    return unit


def _human_text(stages: dict[int, dict], unit_id: str = "M-01-T02") -> str:
    messages = build_procedure_doc_messages(unit_context(_unit(stages, unit_id), stages))
    assert messages[0].content == PROCEDURE_DOC_SYSTEM_PROMPT
    return str(messages[1].content)


def output() -> ProcedureDocGenerationOutput:
    """空の行と、範囲の外の段階を含む出力。"""
    return procedure_doc_output(
        purpose=" 予約を登録できる ",
        files=[
            GeneratedUnitFile(
                path="app/api/routes/reservations.py",
                kind="module",
                responsibility="予約の API",
                basis="段階4",
            ),
            GeneratedUnitFile(path=" ", kind="test", responsibility="", basis=""),
        ],
        notes=["マイグレーションを1本足す", " "],
        findings=[
            GeneratedFinding(level="critical", target="段階3", message="項目が無い", fix_stage=3),
            GeneratedFinding(level="minor", target="手順書", message="範囲外", fix_stage=9),
            GeneratedFinding(level="major", target="x", message=" ", fix_stage=1),
        ],
    )


def test_smoke_build_messages_and_convert_output() -> None:
    """統合スモーク: 単位の材料からメッセージを作り、出力を手順書にする。"""
    stages = document_stage_models()

    text = _human_text(stages)
    procedure = to_unit_procedure(_unit(stages), output())

    assert text.startswith("## 対象の単位\n- 単位: M-01-T02 予約を登録する")
    assert procedure.unit_id == "M-01-T02"
    assert procedure.title == "予約を登録する"


def test_system_prompt_ends_with_naming_rules() -> None:
    assert PROCEDURE_DOC_SYSTEM_PROMPT.endswith(NAMING_RULES)
    assert "推測で埋めずに findings に挙げる" in PROCEDURE_DOC_SYSTEM_PROMPT


def test_messages_carry_unit_expanded_refs_and_common_sections() -> None:
    text = _human_text(document_stage_models())

    assert "- 種別: 機能" in text
    assert "- 依存する単位: M-01-T01" in text
    assert "- モジュール(段階4): app/api/routes/reservations.py" in text
    assert "## 参照する設計\n\n### 段階5 F-01 予約を登録する" in text
    assert "### 段階6 L-01 create_reservation" in text
    assert "## 共通の方針(段階7)\n\n### 07章 横断事項" in text
    assert "### 段階7 開発環境\n\nPython 3.13 と PostgreSQL" in text


def test_unresolved_refs_are_listed_as_missing() -> None:
    stages = document_stage_models()
    stages[5] = {"procedures": []}

    text = _human_text(stages)

    assert "- 段階5 F-01 予約を登録する: 設計にありません" in text


def test_base_unit_has_no_refs() -> None:
    text = _human_text(document_stage_models(), "M-01-T01")

    assert "- 種別: 基盤" in text
    assert "- 環境・設定のファイル(例): Dockerfile" in text
    assert "## 参照する設計\n\n(ありません)" in text


def test_to_unit_procedure_strips_and_drops_empty_rows() -> None:
    procedure = to_unit_procedure(_unit(document_stage_models()), output())

    assert procedure.purpose == "予約を登録できる"
    assert [(f.path, f.kind) for f in procedure.files] == [
        ("app/api/routes/reservations.py", "module")
    ]
    assert procedure.notes == ["マイグレーションを1本足す"]
    assert [t.stub for t in procedure.tests] == ["スタブ不要"]
    assert procedure.verify == ["テストが通る"]


def test_fix_stage_out_of_range_becomes_stage8() -> None:
    """直す先の段階は 1〜7 だけ。範囲の外は段階8(「段階Nで直す」を出さない)にする。"""
    procedure = to_unit_procedure(_unit(document_stage_models()), output())

    assert [(f.level, f.fix_stage) for f in procedure.findings] == [("critical", 3), ("minor", 8)]
