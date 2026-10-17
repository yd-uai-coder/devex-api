"""テスト仕様書の AI の下書きの入出力のテスト。

SUT は`app/documents/test_spec/drafting.py`の`build_test_spec_messages`・`to_draft`、ドライバは
このテスト。スタブ不要 ── 対象が純粋関数で、上流の model と構造化出力だけから決まるため。
"""

from tests.fixtures.spec_documents import CREATE_API, spec_external, spec_output, spec_requirements

from app.documents.test_spec import required_tests
from app.documents.test_spec.drafting import SYSTEM_PROMPT, build_test_spec_messages, to_draft


def _human(**kwargs) -> str:
    external = spec_external()
    external.screens[0].ui_spec = "- 入力: 日付(UI 仕様の本文)"
    obligations = required_tests(spec_requirements(), external)
    messages = build_test_spec_messages("# 1. 要件定義書\nFR-001", external, obligations)
    assert messages[0].content == SYSTEM_PROMPT
    return str(messages[1].content)


def test_messages_have_obligations_screens_apis_and_requirements():
    human = _human()
    assert "## 必要なテスト\n種類|対象|観点|理由|要件|画面\n結合|GET /api/v1/reservations|" in human
    assert '"id": "SCR-001"' in human
    assert '"path": "/api/v1/reservations"' in human
    assert human.endswith("## 要件定義書\n# 1. 要件定義書\nFR-001")


def test_messages_drop_ui_spec_to_keep_input_small():
    assert "UI 仕様の本文" not in _human()


def test_messages_without_external_design():
    messages = build_test_spec_messages("# 1. 要件定義書", None, [])
    human = str(messages[1].content)
    assert "## 必要なテスト\nなし" in human
    assert "## 外部設計書の API\nなし" in human


def test_to_draft_keeps_policy_and_cases():
    draft = to_draft(spec_output())
    assert draft.policy.target == "API 1本"
    assert [(c.level, c.target, c.viewpoint) for c in draft.cases] == [
        ("integration", CREATE_API, "正常"),
        ("integration", CREATE_API, "異常"),
    ]
    assert draft.cases[0].req_ids == ("FR-001",)


def test_prompt_puts_japanese_rule_before_the_rules():
    # 必要なテストの行が多いと英語に流れたので、言語の規則は規則の一覧より前に置く
    assert SYSTEM_PROMPT.index("すべて日本語で書く") < SYSTEM_PROMPT.index("規則:")


def test_prompt_asks_requirement_tests_to_split_into_behaviors():
    # 要件のテストが「要件どおり〜が提供されること」と浅くなったので、振る舞いに分けて書かせる。
    # ほかの行の書き方は変えない(足した指示がほかを押し出さないように)
    assert "要件の文を確かめられる振る舞い" in SYSTEM_PROMPT
    assert "「要件どおり」「提供されること」「正しく動作すること」とだけ書かない" in SYSTEM_PROMPT
    assert "ほかの行の書き方は上の規則のまま" in SYSTEM_PROMPT
