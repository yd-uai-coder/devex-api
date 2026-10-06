"""段階1(機能一覧)のAIの下書きの入出力(純粋関数。docs/external_design.md 2.7節)。

AIには、外部設計書から「処理」を列挙させるだけにする。処理IDと機能グループの初期値は書かせない
(function_list.merge_draft が前の版との突き合わせとパスから決定的に決める)。AIに決めさせると、
再生成のたびにIDや初期値が揺れて、後の段階の参照や人の確定値が壊れるため。
"""

from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.function_list import FunctionDraft
from app.detailed_design.prompt_rules import NAMING_RULES


class GeneratedFunction(BaseModel):
    """AIが下書きする処理1件(構造化出力のスキーマ)。"""

    name: str = Field(description="処理の名称。「〜する」「〜を返す」の動詞句")
    kind: Literal["API", "API+バッチ", "バッチ", "画面", "その他"] = Field(
        description="API: リクエストで完結する / API+バッチ: 受け付けて裏で続ける / "
        "バッチ: 定期実行など / 画面: サーバーを呼ばず画面の中で完結する演算・描画 / その他"
    )
    trigger: str = Field(
        description="APIは『POST /api/v1/projects』の形(外部設計書2.6のメソッドとパス)。"
        "画面は『SCR-005: プレビューの切り替え』の形(画面ID: 操作)。それ以外はバッチ名・契機"
    )
    screens: list[str] = Field(description="関連画面の画面ID(SCR-001など)。無ければ空")
    summary: str = Field(description="処理の概要を1文で")
    group_hint: str = Field(
        default="",
        description="APIでない処理(バッチ・画面・その他)だけ、機能グループの提案"
        "(APIは空にする)",
    )


class FunctionListGenerationOutput(BaseModel):
    """段階1の下書きの構造化出力。"""

    functions: list[GeneratedFunction]


SYSTEM_PROMPT = (
    "あなたは詳細設計の担当者です。【外部設計書】から、このシステムの処理(機能)を一覧にします。\n"
    "規則:\n"
    "- 外部設計書の『2.6 API一覧』のAPIは、1行を1つの処理にして、すべて含める。"
    "triggerには、そのメソッドとパスをそのまま『POST /api/v1/projects』の形で書く\n"
    "- APIで受け付けて裏で処理を続けるもの(非同期生成など)は kind を『API+バッチ』にする\n"
    "- APIを持たない処理(定期実行・起動時の処理など)が外部設計書から読み取れれば、"
    "kind を『バッチ』か『その他』にして加え、group_hint に機能グループを提案する\n"
    "- 外部設計書の『2.3 主要画面のUI/UX仕様』から、サーバーを呼ばずに画面の中で完結する"
    "処理のうち、非自明な演算・描画(集計・変換・図の描画など)だけを kind『画面』で加える。"
    "triggerは『SCR-005: プレビューの切り替え』の形にし、group_hint に機能グループを提案する。"
    "入力欄の表示や画面遷移のような単純なものは加えない\n"
    "- screens には、外部設計書の画面一覧(2.2)の画面IDを書く\n"
    "- 処理IDは書かない(システムが振る)。並びは、利用者の操作の流れに沿った順にする"
    + NAMING_RULES
)


def build_function_list_messages(external_design: str) -> list[BaseMessage]:
    """段階1の下書きの入力。外部設計書の表示中の版の全文を渡す(画面一覧とAPI一覧の両方が要るため)。"""
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"## 外部設計書\n{external_design}"),
    ]


def to_drafts(output: FunctionListGenerationOutput) -> list[FunctionDraft]:
    """構造化出力を、merge_draft の入力に変える。名称が空の行は捨てる。"""
    return [
        FunctionDraft(
            name=item.name,
            kind=item.kind,
            trigger=item.trigger,
            screens=tuple(s.strip() for s in item.screens if s.strip()),
            summary=item.summary,
            group_hint=item.group_hint,
        )
        for item in output.functions
        if item.name.strip()
    ]
