"""設計レビューの入力・出力・判定(app/review。純粋関数)のテスト。

SUT は app/review の各関数、ドライバは pytest。スタブ不要 ── 対象が純粋(副作用なし)で、
外部依存(DB・LLM)を呼ばないため。
"""

from langchain_core.messages import HumanMessage, SystemMessage
from tests.fixtures.requirements import requirements_output
from tests.fixtures.review import review_finding, review_output

from app.documents.issues import DocumentIssue
from app.documents.requirements import build_model, validate_requirements
from app.documents.requirements.drafting import to_draft
from app.hearing import PointState, initial_states
from app.review import (
    REVIEW_SYSTEM_PROMPT,
    ReviewFindingOut,
    build_points_table,
    build_review_messages,
    format_known_issues,
    known_issues,
    render_findings,
    review_effective,
    sort_findings,
)


def _states() -> dict[str, PointState]:
    states = initial_states()
    states["security"] = PointState("security", "adopted", "ログインをMVPに入れる")
    return states


def test_review_effective_only_when_allowed_with_setting_on() -> None:
    assert review_effective(review_allowed=True, review_enabled=True)
    assert not review_effective(review_allowed=True, review_enabled=False)
    # 使えないアカウント(superuser でも自分のキーも無い)は、保存値がオンでもオフ
    assert not review_effective(review_allowed=False, review_enabled=True)


def test_points_table_has_key_status_and_required_columns() -> None:
    table = build_points_table(_states(), "public")

    assert table.startswith("規模: 公開サービス")
    assert "| キー | 観点 | 必須 | 状態 | 内容 |" in table
    assert "| security | セキュリティ | 必須 | AI の提案を採用 | ログインをMVPに入れる |" in table
    # 必須でない観点は空欄でなく「任意」と書く(未決のまま落ちていると読めるように)
    assert "| schedule | 体制・期間 | 任意 | 未決 |  |" in table


def test_points_table_without_scale_says_undecided() -> None:
    assert build_points_table(initial_states(), None).startswith("規模: 未決")


def test_known_issues_runs_document_validation_and_skips_missing_models() -> None:
    model = build_model(to_draft(requirements_output()), None, _states(), None)

    issues = known_issues(
        {
            "requirements": model.model_dump(mode="json"),
            "external_design": None,  # model の無い版
            "unknown": {},
        }
    )

    assert issues == [("requirements", i) for i in validate_requirements(model)]
    # 壊れた model は飛ばす
    assert known_issues({"requirements": {"rows": "not a list"}}) == []


def test_format_known_issues_lines_and_none() -> None:
    issue = DocumentIssue(
        level="critical", code="FR_NO_SCREEN", message="画面が無い", targets=("FR-001",)
    )

    assert format_known_issues([]) == "- なし"
    assert format_known_issues([("external_design", issue)]) == (
        "- [外部設計書] 最重要 FR_NO_SCREEN: 画面が無い(対象: FR-001)"
    )


def test_review_messages_contain_documents_points_and_known_issues() -> None:
    messages = build_review_messages(
        {"requirements": "# 要件\nFR-001", "external_design": "# 外部\nSCR-001"},
        _states(),
        "public",
        [],
    )

    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == REVIEW_SYSTEM_PROMPT
    assert isinstance(messages[1], HumanMessage)
    human = str(messages[1].content)
    assert human.index("## 要件定義書") < human.index("## 外部設計書")
    assert "# ヒアリングの観点の表" in human and "| security |" in human
    assert human.endswith("# 決定的な検証の指摘(既知)\n\n- なし")


def test_review_prompt_checks_links_constraints_and_does_not_cap_findings() -> None:
    # 当たりにくかった型: 要件の結び先の誤り・任意の未決の観点・制約条件の反映
    assert "8. 対応の正しさ" in REVIEW_SYSTEM_PROMPT
    assert "必須でなくても" in REVIEW_SYSTEM_PROMPT
    assert "制約条件(外部サービスの上限など)" in REVIEW_SYSTEM_PROMPT
    assert "件数を絞らない" in REVIEW_SYSTEM_PROMPT


def test_review_prompt_asks_for_unwritten_facts_with_point_key() -> None:
    # D-NAT-1(セキュリティが浅いまま採用され、アクセス制御が要件に無い)を拾わせる指示
    assert "書かれていないことを見る" in REVIEW_SYSTEM_PROMPT
    assert "point_key" in REVIEW_SYSTEM_PROMPT


def test_sort_and_render_findings_by_level() -> None:
    findings = review_output().findings

    assert [f.level for f in sort_findings(findings)] == ["critical", "major", "minor"]
    md = render_findings(findings)
    assert md.startswith("## 設計レビュー\n指摘 3件(最重要 1・中程度 1・軽微 1)")
    assert md.index("最重要 / 設計") < md.index("中程度 / ヒアリング・観点 security")
    assert "   - 引用: POST /api/v1/projects … 200 OK" in md


def test_render_without_findings() -> None:
    assert render_findings([]) == "## 設計レビュー\n指摘はありません。"


def test_unknown_point_key_becomes_null() -> None:
    finding = review_finding(cause="hearing", point_key="security")
    assert finding.point_key == "security"
    assert review_finding(cause="hearing", point_key="アクセス制御").point_key is None


def test_point_key_is_required_in_output_schema() -> None:
    # 省略できる項目にすると、モデルが書かないことがある(null は書ける)
    schema = ReviewFindingOut.model_json_schema()
    assert "point_key" in schema["required"]
    assert "documents" in schema["required"]


def test_documents_drop_unknown_and_follow_upstream_order() -> None:
    finding = review_finding(
        documents=("implementation_plan", "設計書", "requirements", "requirements")
    )
    assert finding.documents == ["requirements", "implementation_plan"]


def test_review_prompt_asks_for_documents_to_rewrite() -> None:
    assert "documents には" in REVIEW_SYSTEM_PROMPT
    assert "要件定義書を\n  入れない" in REVIEW_SYSTEM_PROMPT
