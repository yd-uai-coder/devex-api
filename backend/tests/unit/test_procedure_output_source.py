"""実装手順書の出力の入力と、未定義・要決定の集約(純粋関数)のテスト。

SUT は`app/detailed_design/procedure_output/source.py`の純粋関数、ドライバはこのテスト。
スタブ不要 ── 対象は入力の段階の内容(辞書)・検証の指摘・要件定義の本文だけから決まり、DB や
LLM を呼ばないため。
"""

from tests.fixtures.detailed_design import (
    REQUIREMENTS_WITH_SCOPE,
    procedure_doc_model,
    sample_procedure_source,
)

from app.detailed_design import PlanModel, find_unit
from app.detailed_design.procedure_output import (
    collect_findings,
    count_by_level,
    out_of_scope_lines,
    unit_filename,
    unit_findings,
)
from app.detailed_design.procedure_output.source import (
    UnitFinding,
    count_text,
    fix_stage_text,
    fix_target_text,
)
from app.detailed_design.validation import StageIssue


def _check(level: str | None, unit: str | None, code: str = "X") -> StageIssue:
    return StageIssue(
        severity="warning" if level else "error",
        code=code,
        message=f"{code} の指摘",
        target="F-01",
        level=level,  # type: ignore[arg-type]
        fix_stage=5 if level else None,
        unit=unit,
    )


def test_source_keeps_plan_order_and_documented_units() -> None:
    """統合スモーク: 単位は段階7の並び順、手順書と展開は手順書のある単位だけ。"""
    source = sample_procedure_source()

    assert [u.unit_id for u in source.units] == ["M-01-T01", "M-01-T02"]
    assert list(source.procedures) == ["M-01-T02"]
    assert list(source.contexts) == ["M-01-T02"]
    assert source.approved
    assert source.milestone_name(source.units[1]) == "予約の登録"
    assert source.out_of_scope == ("Should have(重要): 予約の履歴", "Won't have(見送り): 決済")


def test_mismatched_procedure_is_not_output() -> None:
    """段階7とタスク名の合わない手順書(作り直しの対象)は、出力に使わない。"""
    source = sample_procedure_source(model=procedure_doc_model(title="古い名前"))

    assert source.procedures == {}
    assert source.findings == ()


def test_findings_merge_checks_and_ai_in_level_order() -> None:
    """検証の指摘(重要度のあるもの)と AI の指摘を、重要度の順(同じ重要度は検証 → AI)に並べる。
    重要度の無い検証の指摘(手順書のエラー)は入れない。"""
    issues = (
        _check("minor", "M-01-T02", "MINOR"),
        _check(None, "M-01-T02", "ERROR"),
        _check("critical", None, "GLOBAL"),
    )
    source = sample_procedure_source(issues=issues)

    assert [(f.level, f.origin, f.unit) for f in source.findings] == [
        ("critical", "check", None),
        ("critical", "ai", "M-01-T02"),
        ("minor", "check", "M-01-T02"),
    ]
    assert [f.message for f in unit_findings(source, None)] == ["GLOBAL の指摘"]
    assert len(unit_findings(source, "M-01-T02")) == 2
    assert count_by_level(source.findings) == {"critical": 2, "major": 0, "minor": 1}


def test_collect_findings_defaults_fix_stage_to_8() -> None:
    issue = StageIssue(severity="warning", code="X", message="m", level="major")

    (finding,) = collect_findings([issue], [])

    assert (finding.fix_stage, finding.target, fix_stage_text(finding.fix_stage)) == (8, "", "—")
    assert fix_stage_text(4) == "段階4"


def test_count_text() -> None:
    source = sample_procedure_source(issues=(_check("minor", "M-01-T02"),))

    assert count_text(unit_findings(source, "M-01-T02")) == "最重要1・軽微1"
    assert count_text([]) == "なし"


def test_out_of_scope_lines_without_section() -> None:
    assert out_of_scope_lines("# 要件定義書\n\n## 1.3 目的\n") == []
    assert out_of_scope_lines("## 1.4 機能要件\n- **Could have**: \n") == []
    assert out_of_scope_lines(REQUIREMENTS_WITH_SCOPE)[0] == "Should have(重要): 予約の履歴"


def test_unit_filename_replaces_unsafe_characters() -> None:
    plan = PlanModel.model_validate(sample_procedure_source().plan.model_dump())
    unit = find_unit(plan, "M-01-T02")
    assert unit is not None

    assert unit_filename(unit) == "M-01-T02_予約を登録する.md"
    renamed = unit.task.model_copy(update={"title": "登録 / 取消: 予約?"})
    assert unit_filename(unit.__class__(unit.unit_id, unit.milestone, renamed)) == (
        "M-01-T02_登録_取消_予約.md"
    )
    blank = unit.task.model_copy(update={"title": "  "})
    assert unit_filename(unit.__class__(unit.unit_id, unit.milestone, blank)) == "M-01-T02.md"


def test_fix_target_prefers_document() -> None:
    """直す先は、文書があれば文書の名前(簡易モード)、無ければ段階。"""
    finding = UnitFinding("major", "check", None, "DF-1", "x", 8, "internal_design")

    assert fix_target_text(finding) == "内部設計書"
    assert fix_target_text(UnitFinding("major", "check", None, "F-01", "x", 5)) == "段階5"
