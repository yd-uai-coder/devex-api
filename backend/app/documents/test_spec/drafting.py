"""テスト仕様書の AI の下書きの入出力(純粋関数)。

AI には、コードが決めた「必要なテスト」(`obligations.required_tests`)の各項目について、前提・
手順・期待結果を書かせる。入力は要件定義書の md と、外部設計書の画面・API の行(UI 仕様などの本文は
渡さない。入力を小さく保つ)。テストの ID・処理・単位はコードが付ける(`assemble.build_model`)。
レビューの対象にしないので、修正指示は受け取らない。
"""

from collections.abc import Sequence
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.documents.external_design.model import ExternalDesignModel
from app.documents.revision import NONE_TEXT, rows_text
from app.documents.test_spec.assemble import CaseDraft, TestSpecDraft
from app.documents.test_spec.model import SpecPolicy, Viewpoint
from app.documents.test_spec.obligations import Obligation, obligation_rows


class GeneratedCase(BaseModel):
    level: Literal["integration", "system"] = Field(
        description="integration = 結合テスト(API 1つをルーターから DB まで通す)、"
        "system = 総合テスト(画面の操作の流れ・非機能要件・運用)"
    )
    target: str = Field(
        description="結合テストは【API】のメソッドとパス(POST /api/v1/x の形)。総合テストは"
        "画面の流れ(SCR-001 → SCR-002)・非機能要件の項目・運用など"
    )
    viewpoint: Viewpoint = Field(description="観点")
    precondition: str = Field(description="前提(データ・ログインの状態など)。無ければ空")
    steps: str = Field(description="手順・入力(具体的な値)")
    expected: str = Field(
        description="期待結果(HTTP ステータス・保存される / されないデータ・表示)"
    )
    design_gap: str = Field(
        description="期待結果を決めるのに要るのに、設計で決まっていないこと(上限値・権限・状態など)。"
        "決まっていれば空"
    )
    req_ids: list[str] = Field(description="確かめる要件の ID(【要件定義書】の FR-001・NFR-001)")
    screens: list[str] = Field(description="関係する画面ID(SCR-001)。無ければ空")


class TestSpecGenerationOutput(BaseModel):
    """テスト仕様書の下書きの構造化出力。"""

    __test__ = False  # pytest に集めさせない(名前が Test で始まるため)

    target: str = Field(description="テストの対象(API の本数・画面・非機能要件の範囲)")
    out_of_scope: str = Field(description="範囲外(Should 以下の要件・MVP の後と決めたもの)")
    environment: str = Field(description="テストの環境(DB・外部サービスの差し替え方)")
    done_criteria: str = Field(description="完了条件")
    cases: list[GeneratedCase] = Field(description="テスト(結合 → 総合の順)")


SYSTEM_PROMPT = (
    "あなたは経験豊富なテストエンジニアです。【要件定義書】と【外部設計書の画面・API】から、"
    "結合テスト(API 単位)と総合テスト(画面の流れ・非機能)のテスト仕様書を書きます。\n"
    "文章(対象・範囲外・環境・完了条件・前提・手順・期待結果・design_gap)はすべて日本語で書く。"
    "英語の文にしない(API のパス・項目名・値はそのまま)。\n"
    "規則:\n"
    "- 【必要なテスト】の各行について、少なくとも1件のテストを書く。行の種類・対象・観点を"
    "そのまま使い、理由に沿った手順と期待結果を書く\n"
    "- 技法: 正常は同値分割(有効な値の代表)、異常はエラーの起こし方、境界は境界値分析"
    "(下限・上限・その±1・空)、セキュリティは認証・認可(ほかの利用者のリソース・トークン無し)、"
    "非機能は測り方と合格の値\n"
    "- 手順・入力は具体的な値で書く。期待結果は HTTP ステータスと、保存される / されない"
    "データを書く\n"
    "- 期待結果に要る値・振る舞いが設計で決まっていなければ、推測で埋めずに design_gap に書く\n"
    "- 要件ID・画面ID・API は入力にあるものだけを使い、作らない\n"
    "- 【必要なテスト】に無いテストを足してもよい(要件を確かめるのに要るもの)"
)


def build_test_spec_messages(
    requirements_md: str,
    external: ExternalDesignModel | None,
    obligations: Sequence[Obligation],
) -> list[BaseMessage]:
    rows = obligation_rows(obligations)
    table = "種類|対象|観点|理由|要件|画面\n" + "\n".join(rows) if rows else NONE_TEXT
    screens = external.screens if external else []
    apis = external.apis if external else []
    screen_rows = [s.model_copy(update={"ui_spec": ""}) for s in screens]
    human = (
        f"## 必要なテスト\n{table}\n\n"
        f"## 外部設計書の画面\n{rows_text(screen_rows)}\n\n"
        f"## 外部設計書の API\n{rows_text(apis)}\n\n"
        f"## 要件定義書\n{requirements_md}"
    )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=human)]


def to_draft(output: TestSpecGenerationOutput) -> TestSpecDraft:
    return TestSpecDraft(
        policy=SpecPolicy(
            target=output.target,
            out_of_scope=output.out_of_scope,
            environment=output.environment,
            done_criteria=output.done_criteria,
        ),
        cases=tuple(
            CaseDraft(
                c.level,
                c.target,
                c.viewpoint,
                c.precondition,
                c.steps,
                c.expected,
                c.design_gap,
                tuple(c.req_ids),
                tuple(c.screens),
            )
            for c in output.cases
        ),
    )
