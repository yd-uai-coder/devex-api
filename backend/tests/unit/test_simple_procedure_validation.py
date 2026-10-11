"""簡易モードの段階8の検証(実装可能性チェック)のテスト。

SUT は`app/detailed_design/validation.py`の`validate_procedure_doc`(簡易モードの分岐)、ドライバは
このテスト。スタブ不要 ── 入力は`StageSources`(モードと4文書の本文)で、DB や LLM を呼ばないため。
"""

from tests.fixtures.simple_procedure import (
    INTERNAL_DESIGN_MD,
    LAYERED_INTERNAL_DESIGN_MD,
    OLD_PLAN_MD,
    PLAN_MD,
    simple_document_models,
    simple_documents,
    simple_procedure_doc_model,
)

from app.detailed_design.validation import StageIssue, StageSources, validate_procedure_doc


def _check(
    model: dict | None = None, *, requirements: dict | None = None, **documents: str
) -> list[StageIssue]:
    sources = StageSources(
        documents=simple_documents(**documents),
        mode="simple",
        document_models=simple_document_models(requirements),
    )
    return validate_procedure_doc(model or {}, sources)


def _codes(issues: list[StageIssue]) -> list[tuple[str, str | None]]:
    return [(i.code, i.fix_document) for i in issues]


def test_consistent_documents_have_no_findings() -> None:
    """統合スモーク: WBS と内部設計書がそろっていれば、手順書があっても指摘0件。"""
    assert _check(simple_procedure_doc_model()) == []


def test_unit_mismatch_is_an_error_named_after_the_plan() -> None:
    """手順書の単位が WBS と合わなければエラー(承認を止める)。文言は実装計画書を指す。"""
    plan = PLAN_MD.replace("[機能] 予約を登録する", "[機能] 予約を受け付ける")

    issues = _check(simple_procedure_doc_model(), implementation_plan=plan)

    assert [(i.severity, i.code) for i in issues] == [("error", "UNIT_MISMATCH")]
    assert "実装計画書の M-01-T02" in issues[0].message
    assert "「実装計画書に無い手順書」で、今の単位へ付け替えるか消してください" in issues[0].message


def test_old_documents_ask_to_regenerate() -> None:
    """旧形式の WBS とモジュール一覧の無い内部設計書は、最重要の指摘で再生成を促す。"""
    internal = INTERNAL_DESIGN_MD.replace("### モジュール一覧", "### 構成")

    issues = _check(implementation_plan=OLD_PLAN_MD, internal_design=internal)

    assert [(i.code, i.level, i.fix_document) for i in issues[:2]] == [
        ("WBS_MISSING", "critical", "implementation_plan"),
        ("NO_MODULE_LIST", "critical", "internal_design"),
    ]
    # 単位が無いので、どの DF も Must の要件も計画に入っていない
    assert [i.code for i in issues[2:]] == [
        "UNPLANNED_DATAFLOW",
        "UNPLANNED_DATAFLOW",
        "MUST_REQUIREMENT_UNPLANNED",
    ]
    assert all(i.severity == "warning" and i.fix_stage == 8 for i in issues)


def test_plan_gaps_point_to_the_plan() -> None:
    """依存・DF の不足を、直す先の文書つきの警告にする。モジュール・ファイルは指摘しない。"""
    plan = (
        PLAN_MD.replace("  - 処理: DF-2\n", "  - 処理: DF-9\n")
        .replace("  - 依存: M-01-T02\n", "  - 依存: M-03-T01\n")
        .replace("  - 処理: DF-1\n", "")
        .replace("  - モジュール: app/main.py\n", "  - モジュール: app/other.py, app\n")
    )

    issues = _check(simple_procedure_doc_model(module="app/x.py"), implementation_plan=plan)

    assert _codes(issues) == [
        ("FEATURE_WITHOUT_DATAFLOW", "implementation_plan"),
        ("UNKNOWN_DEPENDENCY", "implementation_plan"),
        ("UNKNOWN_DATAFLOW", "implementation_plan"),
        ("UNPLANNED_DATAFLOW", "implementation_plan"),
        ("UNPLANNED_DATAFLOW", "implementation_plan"),
    ]
    assert [i.unit for i in issues] == ["M-01-T02", "M-02-T01", "M-02-T01", None, None]


def test_modules_are_checked_only_to_the_layer() -> None:
    """層ごとにまとめた一覧でも、モジュール・手順書のファイル・層に当たらないファイルは指摘しない。"""
    plan = PLAN_MD.replace(
        "  - モジュール: app/main.py\n", "  - モジュール: backend/Dockerfile, backend/app/core\n"
    )

    issues = _check(
        simple_procedure_doc_model(module="backend/app/api/reservations.py"),
        implementation_plan=plan,
        internal_design=LAYERED_INTERNAL_DESIGN_MD,
    )

    assert issues == []


def test_requirement_gaps_point_to_the_plan() -> None:
    """要件の追跡: 単位の無い Must の要件(中程度)・要件の無い機能の単位・要件定義書に無い
    要件ID(軽微)。直す先はどれも実装計画書。"""
    plan = PLAN_MD.replace("  - 要件: FR-001\n", "").replace("要件: FR-002", "要件: FR-009")

    issues = _check(simple_procedure_doc_model(), implementation_plan=plan)

    assert [(i.code, i.level, i.target, i.unit) for i in issues] == [
        ("MUST_REQUIREMENT_UNPLANNED", "major", "FR-001", None),
        ("UNLINKED_UNIT", "minor", "M-01-T02", "M-01-T02"),
        ("UNKNOWN_REQUIREMENT_ID", "minor", "FR-009", "M-02-T01"),
    ]
    assert {i.fix_document for i in issues} == {"implementation_plan"}


def test_requirements_without_ids_ask_to_regenerate() -> None:
    """要件定義書に model が無い(Phase 36 より前の版)なら、軽微な1件だけ。"""
    issues = _check(simple_procedure_doc_model(), requirements={})

    assert [(i.code, i.level) for i in issues] == [("REQUIREMENT_IDS_MISSING", "minor")]
