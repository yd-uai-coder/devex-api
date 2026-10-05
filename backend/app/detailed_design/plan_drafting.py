"""段階7(横断事項と実装計画)のAIの下書きの入出力(純粋関数。docs/external_design.md 2.7節)。

1回の生成で、次の2種類を順に呼ぶ(段階3・4と同じ形):

- 横断事項(07): 入力は、要件定義書・外部設計書・詳細設計書の md(01〜06章)(LLM 1回)。
- 実装計画: 入力は、要件定義書・詳細設計書の md・先に作った横断事項・処理ID の一覧(LLM 1回)。

詳細設計書の md は、出力と同じ組み立て(`to_markdown`)で作る。簡易ドキュメント
モードの実装計画が「要件定義+内部設計書」を入力にするのに当たり、承認済みの段階1〜6の内容を
人が読むのと同じ形で渡せる。処理ID の一覧を別に渡すのは、計画の漏れ(どのマイルストーンにも
入らない処理)を減らすため。
"""

from collections.abc import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.function_list import FunctionRow
from app.detailed_design.plan import (
    CROSSCUTTING_TOPICS,
    CrossCuttingRow,
    Milestone,
    PlanModel,
    PlanTask,
    Priority,
    Risk,
    TaskArea,
)
from app.detailed_design.prompt_rules import NAMING_RULES


class GeneratedCrossCutting(BaseModel):
    """AIが下書きする横断事項の1行。"""

    topic: str = Field(description="項目の名前(例: 例外と HTTP)")
    policy: str = Field(description="このシステムでの方針(2〜4文。具体的な値・規則を含める)")
    modules: list[str] = Field(
        description="この方針に関わるファイルのパスの例(【詳細設計書】04 のモジュール一覧のパスや、"
        "設定のファイル)"
    )


class CrossCuttingGenerationOutput(BaseModel):
    """横断事項の構造化出力。"""

    crosscutting: list[GeneratedCrossCutting]


class GeneratedTask(BaseModel):
    """AIが下書きするタスク1つ。"""

    area: TaskArea = Field(description="区分")
    title: str = Field(description="タスクの内容(1文)")
    modules: list[str] = Field(
        description="作る・直すファイルのパスの例(モジュール一覧のパスや、Dockerfile・"
        "docker-compose.yml などの環境・設定のファイル)。無ければ空"
    )
    function_ids: list[str] = Field(description="このタスクで動くようにする処理ID。無ければ空")


class GeneratedMilestone(BaseModel):
    """AIが下書きするマイルストーン1つ。"""

    name: str = Field(description="マイルストーンの名前")
    goal: str = Field(description="完了の基準(何ができるようになるか)を1文で")
    priority: Priority = Field(description="MoSCoW の優先度")
    function_ids: list[str] = Field(description="このマイルストーンで動くようにする処理ID")
    tasks: list[GeneratedTask]


class GeneratedRisk(BaseModel):
    """AIが下書きするリスク1つ。"""

    risk: str = Field(description="想定される技術的・スケジュール的なリスク")
    mitigation: str = Field(description="対策(納期が厳しいときに削る・後回しにする候補も含む)")


class PlanGenerationOutput(BaseModel):
    """実装計画の構造化出力。"""

    milestones: list[GeneratedMilestone]
    environment: str = Field(
        description="開発環境・CI/CD・事前準備(ツール、リポジトリ構成、自動テストの方針)"
    )
    risks: list[GeneratedRisk]


CROSSCUTTING_SYSTEM_PROMPT = (
    "あなたは詳細設計の担当者です。【要件定義書】【外部設計書】【詳細設計書】から、"
    "詳細設計書の『07 横断事項』(全処理に共通する実装の方針)を作成します。\n"
    "規則:\n"
    f"- 項目は少なくとも {'・'.join(CROSSCUTTING_TOPICS)} を、この順に含める。"
    "設計書から読み取れれば、非同期処理・外部サービスの呼び出し・レート制限なども足す\n"
    "- 方針は、例外と HTTP の状態の対応・トークンの有効期限・commit する層・ログに出す項目の"
    "ように、実装者が迷わない具体的な規則にする。設計書から決まらない値(有効期限の長さなど)は"
    "創作せず、『(要決定)』と書いて決める必要があることを示す\n"
    "- modules には、その方針に関わるファイルのパスを例として書く。【詳細設計書】04 の"
    "モジュール一覧にあるものはそのパスをそのまま使い、設定のファイルも書いてよい"
    "(ライブラリ名は書かない)" + NAMING_RULES
)

PLAN_SYSTEM_PROMPT = (
    "あなたは経験豊富なプロジェクトマネージャーです。【要件定義書】【詳細設計書】【横断事項】"
    "から、開発を安全かつ確実に進めるための実装計画を作成します。\n"
    "規則:\n"
    "- マイルストーンは、動くものを段階的に増やす順に3〜6個にする。最初のマイルストーンは"
    "環境構築と横断事項(認証・例外・ログ)を含める。優先度は要件定義書の MoSCoW に合わせる\n"
    "- 【処理ID の一覧】のすべての処理を、いずれかのマイルストーンの function_ids に入れる\n"
    "- タスクは、マイルストーンごとに準備・バックエンド・フロントエンド・テスト・デプロイの区分で"
    "分ける。modules には作る・直すファイルのパスを例として書く(【詳細設計書】04 のモジュール"
    "一覧にあるものはそのパスをそのまま使い、Dockerfile・docker-compose.yml・CI の設定などの"
    "環境・設定のファイルも書いてよい)。function_ids には【処理ID の一覧】の ID だけを"
    "そのまま書く\n"
    "- リスクは3〜5件。納期・予算を超えそうなときに削る機能・代替手段も対策に書く" + NAMING_RULES
)


def build_crosscutting_messages(
    requirements: str, external_design: str, design_markdown: str
) -> list[BaseMessage]:
    """横断事項の下書きの入力。"""
    content = "\n\n".join(
        [
            f"## 要件定義書\n{requirements}",
            f"## 外部設計書\n{external_design}",
            f"## 詳細設計書\n{design_markdown}",
        ]
    )
    return [SystemMessage(content=CROSSCUTTING_SYSTEM_PROMPT), HumanMessage(content=content)]


def build_plan_messages(
    requirements: str,
    design_markdown: str,
    crosscutting: Sequence[CrossCuttingRow],
    functions: Sequence[FunctionRow],
) -> list[BaseMessage]:
    """実装計画の下書きの入力。横断事項は、先に作った下書き(1回目の LLM の出力)。"""
    crosscutting_text = "\n".join(f"- {row.topic}: {row.policy}" for row in crosscutting)
    functions_text = "\n".join(f"- {row.id}: {row.name}" for row in functions)
    content = "\n\n".join(
        [
            f"## 要件定義書\n{requirements}",
            f"## 詳細設計書\n{design_markdown}",
            f"## 横断事項\n{crosscutting_text or '(ありません)'}",
            f"## 処理ID の一覧\n{functions_text or '(ありません)'}",
        ]
    )
    return [SystemMessage(content=PLAN_SYSTEM_PROMPT), HumanMessage(content=content)]


def to_crosscutting(output: CrossCuttingGenerationOutput) -> list[CrossCuttingRow]:
    """横断事項の構造化出力を、意味モデルの行に変える(空白の整えは`normalize_plan`が行う)。"""
    return [
        CrossCuttingRow(topic=row.topic, policy=row.policy, modules=list(row.modules))
        for row in output.crosscutting
    ]


def to_plan_model(
    crosscutting: Sequence[CrossCuttingRow], output: PlanGenerationOutput
) -> PlanModel:
    """横断事項と実装計画の構造化出力から、段階7の意味モデルを作る(整える前)。"""
    return PlanModel(
        crosscutting=list(crosscutting),
        milestones=[
            Milestone(
                name=m.name,
                goal=m.goal,
                priority=m.priority,
                function_ids=list(m.function_ids),
                tasks=[
                    PlanTask(
                        area=t.area,
                        title=t.title,
                        modules=list(t.modules),
                        function_ids=list(t.function_ids),
                    )
                    for t in m.tasks
                ],
            )
            for m in output.milestones
        ],
        environment=output.environment,
        risks=[Risk(risk=r.risk, mitigation=r.mitigation) for r in output.risks],
    )
