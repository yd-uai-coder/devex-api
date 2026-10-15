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
