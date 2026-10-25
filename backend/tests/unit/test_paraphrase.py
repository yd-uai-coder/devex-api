"""名称を言い換える規則と、言い換えた再生成の出力に対する今の照合の強さのテスト。

SUT: app/ai/llm/paraphrase.py の paraphrase_name / base_name / paraphrase_service /
     paraphrase_requirements / paraphrase_external_design / paraphrase_function_list。
     言い換えた出力の消費者として、段階1 の ID の引き継ぎ(merge_draft)と外部設計書の保護
     (protect_external_design の画面)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。言い換えた出力は、生成の
構造化出力に規則を当てて作る(偽の LLM を介さない)
"""

from tests.fixtures.external_design import external_design_output
from tests.fixtures.requirements import requirements_output

from app.agreements import agreement_items, protect_external_design
from app.ai.llm.paraphrase import (
    PARAPHRASE_SUFFIX,
    base_name,
    paraphrase_external_design,
    paraphrase_function_list,
    paraphrase_name,
    paraphrase_requirements,
    paraphrase_service,
)
from app.detailed_design import merge_draft
from app.detailed_design.drafting import (
    FunctionListGenerationOutput,
    GeneratedFunction,
    to_drafts,
)
from app.documents.external_design import build_model
from app.documents.external_design.drafting import GeneratedIntegration, to_draft
from app.documents.requirements.drafting import GeneratedUserScene


def test_smoke_paraphrased_function_list_keeps_ids() -> None:
    """統合スモーク: 段階1 の下書きを言い換えて組み立て直しても、処理 ID は変わらない(API は
    トリガーで、画面の処理は関連画面で引き継ぐ)。"""
    output = FunctionListGenerationOutput(
        functions=[
            GeneratedFunction(
                name="予約を登録する",
                kind="API",
                trigger="POST /api/v1/reservations",
                screens=["SCR-001"],
                summary="予約を保存する",
            ),
            GeneratedFunction(
                name="トップページ表示",
                kind="画面",
                trigger="",
                screens=["SCR-001"],
                summary="トップページを出す",
            ),
        ]
    )
    first = merge_draft(to_drafts(output))
    second = merge_draft(to_drafts(paraphrase_function_list(output)), first)

    assert [f.id for f in second.functions] == [f.id for f in first.functions]
    assert [f.name for f in second.functions] == [
        f"予約を登録する{PARAPHRASE_SUFFIX}",
        f"トップページ表示{PARAPHRASE_SUFFIX}",
    ]


def test_paraphrase_name_adds_the_suffix_once() -> None:
    assert paraphrase_name(" 社員 ") == f"社員{PARAPHRASE_SUFFIX}"
    assert paraphrase_name(paraphrase_name("社員")) == f"社員{PARAPHRASE_SUFFIX}"
    assert base_name(paraphrase_name("社員")) == "社員"
    assert base_name("社員") == "社員"


def test_paraphrase_service_toggles_the_trailing_api() -> None:
    assert paraphrase_service("地図 API") == "地図"
    assert paraphrase_service("地図API") == "地図"
    assert paraphrase_service("メール配信") == "メール配信 API"


def test_requirements_paraphrase_changes_only_user_names() -> None:
    output = requirements_output()
    output.users = [GeneratedUserScene(user="社員", scene="借りるとき", source="assumed")]

    paraphrased = paraphrase_requirements(output)

    assert [u.user for u in paraphrased.users] == [f"社員{PARAPHRASE_SUFFIX}"]
    assert [u.scene for u in paraphrased.users] == ["借りるとき"]
    assert paraphrased.requirements == output.requirements
    assert output.users[0].user == "社員"


def test_external_design_paraphrase_changes_screen_and_service_names() -> None:
    output = external_design_output()
    output.integrations = [
        GeneratedIntegration(service="地図 API", purpose="", auth="", timing="", req_ids=[])
    ]

    paraphrased = paraphrase_external_design(output)

    assert [s.name for s in paraphrased.screens] == [f"予約画面{PARAPHRASE_SUFFIX}"]
    assert [s.id for s in paraphrased.screens] == ["SCR-001"]
    assert [i.service for i in paraphrased.integrations] == ["地図"]
    assert paraphrased.apis == output.apis


def test_agreed_screen_with_paraphrased_name_goes_back() -> None:
    """画面は画面ID で照合するので、名前を言い換えても合意の形に戻る(今の照合で耐える)。"""
    previous = build_model(to_draft(external_design_output()), None, None)
    agreed = {item.key: item.content for item in agreement_items(None, previous)}
    regenerated = build_model(
        to_draft(paraphrase_external_design(external_design_output())), previous, None
    )

    model, restored = protect_external_design(regenerated, previous, agreed)

    assert [s.name for s in model.screens] == ["予約画面"]
    assert [r.key for r in restored] == ["screen:SCR-001"]
