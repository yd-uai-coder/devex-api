"""実装計画書の AI の下書きの入出力(純粋関数)。

AI には要件定義書・内部設計書から、マイルストーンとタスク(実装手順書の作業単位)の行と本文の節を
書かせる。タスクの ID は依存先を指すためだけに書かせ、保存する ID は並び順から導く
(`assemble.build_model`)。再生成では、前の版のタスク(キー付き)と上流の文書の変わった行を見せ、
同じタスクのキーを`prev_key`で指させ、変えないタスクを`unchanged`と答えさせる
(`app.documents.revision`)。
"""

from collections.abc import Sequence
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.plan_goal import GOAL_RULE
from app.documents.implementation_plan.assemble import (
    ImplementationPlanDraft,
    MilestoneDraft,
    RiskDraft,
    TaskDraft,
)
from app.documents.implementation_plan.model import ImplementationPlanModel
from app.documents.revision import (
    UNCHANGED_DESCRIPTION,
    RevisionInputs,
    changed_rows_text,
    instruction_text,
    revision_rule,
    rows_text,
)

_BODY_GUIDE = "Markdown。見出し(#)は書かない"


class GeneratedTask(BaseModel):
    id: str = Field(
        description="タスクの ID(`M-<2桁>-T<2桁>`。マイルストーンの番号と、その中の01からの連番)"
    )
    kind: Literal["feature", "base"] = Field(
        description="feature = 処理を持つ機能のタスク(その処理のバックエンド・フロントエンド・"
        "テストを含む縦割り)、base = 処理の無い準備・デプロイの基盤のタスク"
    )
    title: str = Field(description="タスク名")
    dataflow_ids: list[str] = Field(
        description="【内部設計書】の処理別データフローの ID(DF-1 など)。機能のタスクは原則1つ"
        "(多くても3つ)、基盤のタスクは空"
    )
    req_ids: list[str] = Field(
        description="そのタスクが実現する【要件定義書】の機能要件の ID(FR-001 など)。"
        "要件定義書に無い ID は作らない。基盤のタスクは空"
    )
    depends_on: list[str] = Field(
        description="先に終わっている必要があるタスクの ID。前に書いたタスクだけを指す"
    )
    modules: list[str] = Field(
        description="作る・直すファイル。【内部設計書】のモジュール一覧のパスを使う"
    )
    config_files: list[str] = Field(
        description="Dockerfile・docker-compose.yml のような環境・設定のファイル"
    )
    prev_key: str = Field(
        default="",
        description="【前の版のタスク】に同じタスクがあれば、その key(U-001 など)。"
        "タスク名を言い直しただけのタスクも同じタスク。新しいタスクは空",
    )
    unchanged: bool = Field(
        default=False,
        description=f"{UNCHANGED_DESCRIPTION}。prev_key のタスクの中身(依存先を除く)を変えない",
    )


class GeneratedMilestone(BaseModel):
    name: str = Field(description="マイルストーン名")
    priority: Literal["Must", "Should", "Could"] = Field(description="MoSCoW の優先度")
    goal: str = Field(description="ゴール(動くようになること)")
    tasks: list[GeneratedTask] = Field(description="タスク")


class GeneratedRisk(BaseModel):
    risk: str = Field(description="想定される技術的・スケジュール的リスク")
    mitigation: str = Field(description="対策(納期・予算超過時のトレードオフ策を含む)")


class ImplementationPlanGenerationOutput(BaseModel):
    """実装計画書の下書きの構造化出力。"""

    phases: str = Field(
        description="4.1 開発フェーズ分割・マイルストーン(フェーズ1(MVP)・フェーズ2 などの段階的"
        "リリース計画と、各フェーズの優先度・含めるマイルストーンの ID(M-01 など)。"
        f"{_BODY_GUIDE})"
    )
    milestones: list[GeneratedMilestone] = Field(description="4.2 タスク分解(WBS)")
    environment: str = Field(
        description="4.3 開発環境・CI/CD・事前準備事項(開発に必要なツール、リポジトリ構成、"
        f"自動テスト・CI/CD 方針。{_BODY_GUIDE})"
    )
    risks: list[GeneratedRisk] = Field(description="4.4 想定リスクと対策")


# 再生成のプロンプトの節の分け方(前の版・きっかけ・参照)。節を足したら、ここにも分ける
REVISION = RevisionInputs(
    previous=("前の版のタスク",),
    triggers={
        "上流の変わった行": "要件定義書・内部設計書で変わった行。その行に関係する行を書き直す",
        "修正指示": "設計レビューの採用した指摘。指示が指す行を書き直す",
    },
    context=("要件定義書", "内部設計書"),
)

SYSTEM_PROMPT = (
    "あなたは経験豊富なプロジェクトマネージャーです。【要件定義書】と【内部設計書】を踏まえ、"
    "開発を安全かつ確実に進めるための実装計画書の中身を、マイルストーンとタスクの行と、説明の節と"
    "して書きます。\n"
    "規則:\n"
    "- タスクは実装手順書の作業単位になる。機能ごとの縦割りにし、1つのタスクにその処理の"
    "バックエンド・フロントエンド・テストを含める。処理の無い準備・デプロイは基盤のタスクにする\n"
    "- 【内部設計書】の処理別データフローは、すべていずれかの機能のタスクに入れる\n"
    "- 【要件定義書】の Must の機能要件は、すべていずれかのタスクの要件に入れる。"
    "Won't have の機能はタスクにしない\n"
    "- タスクの依存先は、前に書いたタスクだけを指す\n"
    f"{GOAL_RULE}"
    "- モジュールは【内部設計書】のモジュール一覧のパスを使う\n"
    "- 要件定義書・内部設計書に無い技術を創作しない\n"
    "- 【前の版のタスク】に同じタスクがあれば、その key を prev_key に書く\n"
    f"{revision_rule(REVISION)}"
)


def build_implementation_plan_messages(
    requirements_md: str,
    internal_design_md: str,
    previous: ImplementationPlanModel | None = None,
    changed_upstream: Sequence[str] = (),
    instruction: str = "",
) -> list[BaseMessage]:
    tasks = [t for m in previous.milestones for t in m.tasks] if previous else []
    human = (
        f"## 前の版のタスク\n{rows_text(tasks)}\n\n"
        f"## 上流の変わった行\n{changed_rows_text(changed_upstream)}\n\n"
        f"## 修正指示\n{instruction_text(instruction)}\n\n"
        f"## 要件定義書\n{requirements_md}\n\n## 内部設計書\n{internal_design_md}"
    )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=human)]


def to_draft(output: ImplementationPlanGenerationOutput) -> ImplementationPlanDraft:
    return ImplementationPlanDraft(
        phases=output.phases,
        milestones=tuple(
            MilestoneDraft(
                m.name,
                m.priority,
                m.goal,
                tuple(
                    TaskDraft(
                        t.id,
                        t.kind,
                        t.title,
                        tuple(t.dataflow_ids),
                        tuple(t.req_ids),
                        tuple(t.depends_on),
                        tuple(t.modules),
                        tuple(t.config_files),
                        t.prev_key,
                        t.unchanged,
                    )
                    for t in m.tasks
                ),
            )
            for m in output.milestones
        ),
        environment=output.environment,
        risks=tuple(RiskDraft(r.risk, r.mitigation) for r in output.risks),
    )
