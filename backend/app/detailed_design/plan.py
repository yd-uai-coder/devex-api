"""段階7 横断事項と実装計画の意味モデル(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」。

段階7は、詳細設計書の 07 横断事項(例外と HTTP・認証・トランザクション・ログ)と、実装計画
(マイルストーン・タスク・開発環境・リスク)を1つの model に持つ(Phase 23 の決定。段階の数を
1〜7 のままにし、07 の元になるデータを段階7で作る)。

- タスクはマイルストーンの中に入れ子で持つ。タスクがマイルストーンの名前で参照すると、改名で参照が
  切れるため(WBS も本来は階層)。
- マイルストーンの番号(`M-01`…)は保存しない。並び順から`milestone_id`で導く(L-ID と同じ)。
- 処理ID は段階1の機能一覧の ID で書く。どのマイルストーンにも入らない処理は、計画の漏れとして
  検証で警告する。
- `modules`(横断事項・タスク)は「作成・変更するファイルの例」で、検証しない。段階4のモジュール
  一覧のパスのほか、`Dockerfile`・`docker-compose.yml` のような環境・設定のファイルも書ける
  (Phase 23 の画面確認後の決定。環境のファイルはモジュール一覧に入らないため)。
"""

from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, Field

from app.detailed_design.procedure import resolve_callee

# 横断事項と実装計画の段階の番号
PLAN_STAGE = 7

Priority = Literal["Must", "Should", "Could"]
TaskArea = Literal["準備", "バックエンド", "フロントエンド", "テスト", "デプロイ"]

PRIORITIES: tuple[Priority, ...] = ("Must", "Should", "Could")
TASK_AREAS: tuple[TaskArea, ...] = ("準備", "バックエンド", "フロントエンド", "テスト", "デプロイ")

# 07 横断事項に必ず書く項目(docs/internal_design.md 3.3節の章構成)。欠けていれば検証で警告する
CROSSCUTTING_TOPICS: tuple[str, ...] = ("例外と HTTP", "認証", "トランザクション", "ログ")


class CrossCuttingRow(BaseModel):
    """07 横断事項の1行。`modules`はその方針に関わるファイルの例(検証しない)。"""

    topic: str
    policy: str = ""
    modules: list[str] = Field(default_factory=list)


class PlanTask(BaseModel):
    """マイルストーンの中のタスク1つ(WBS の1行)。"""

    area: TaskArea = "バックエンド"
    title: str = ""
    modules: list[str] = Field(default_factory=list)
    function_ids: list[str] = Field(default_factory=list)


class Milestone(BaseModel):
    """マイルストーン1つ。`function_ids`はこのマイルストーンで動くようにする処理。"""

    name: str
    goal: str = ""
    priority: Priority = "Must"
    function_ids: list[str] = Field(default_factory=list)
    tasks: list[PlanTask] = Field(default_factory=list)


class Risk(BaseModel):
    """想定リスクと対策の1行。"""

    risk: str
    mitigation: str = ""


class PlanModel(BaseModel):
    """段階7の意味モデル。"""

    crosscutting: list[CrossCuttingRow] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    environment: str = ""
    risks: list[Risk] = Field(default_factory=list)


def milestone_id(index: int) -> str:
    """並び順(0始まり)からマイルストーンの番号を導く(`M-01`…)。"""
    return f"M-{index + 1:02d}"


def planned_function_ids(model: PlanModel) -> set[str]:
    """計画のどこか(マイルストーンかタスク)に書かれた処理ID。"""
    found: set[str] = set()
    for milestone in model.milestones:
        found.update(f.strip() for f in milestone.function_ids)
        for task in milestone.tasks:
            found.update(f.strip() for f in task.function_ids)
    return found


def unplanned_functions(model: PlanModel, function_ids: Sequence[str]) -> list[str]:
    """どのマイルストーン・タスクにも書かれていない処理ID(機能一覧の順)。"""
    planned = planned_function_ids(model)
    return [f for f in function_ids if f not in planned]


def missing_topics(model: PlanModel) -> list[str]:
    """07 横断事項に必ず書く項目のうち、行の無いもの。"""
    topics = {row.topic.strip() for row in model.crosscutting}
    return [topic for topic in CROSSCUTTING_TOPICS if topic not in topics]


def _clean(values: Iterable[str]) -> list[str]:
    """前後の空白を除き、空と重複を捨てる(順は保つ)。"""
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def _modules(values: Iterable[str], module_paths: Sequence[str]) -> list[str]:
    """ファイルの参照を、モジュール一覧のパスにそろえる(段階5の呼び出し先と同じ規則)。
    1行だけに当たるものだけを置き換え、当たらないもの(環境のファイルなど)はそのまま残す。"""
    return _clean(resolve_callee(v, module_paths) for v in values if v.strip())


def normalize_plan(model: PlanModel, module_paths: Sequence[str]) -> PlanModel:
    """AIの下書きを整える。前後の空白・空の行・一覧の重複を除き、モジュールをパスにそろえる。

    - 横断事項は、項目も方針も空の行を捨てる。
    - タスクは、名前が空の行を捨てる。マイルストーンは、名前もタスクも無いものを捨てる。
    - リスクは、リスクも対策も空の行を捨てる。
    """
    crosscutting = [
        CrossCuttingRow(
            topic=row.topic.strip(),
            policy=row.policy.strip(),
            modules=_modules(row.modules, module_paths),
        )
        for row in model.crosscutting
        if row.topic.strip() or row.policy.strip()
    ]
    milestones: list[Milestone] = []
    for milestone in model.milestones:
        tasks = [
            PlanTask(
                area=task.area,
                title=task.title.strip(),
                modules=_modules(task.modules, module_paths),
                function_ids=_clean(task.function_ids),
            )
            for task in milestone.tasks
            if task.title.strip()
        ]
        if not milestone.name.strip() and not tasks:
            continue
        milestones.append(
            Milestone(
                name=milestone.name.strip(),
                goal=milestone.goal.strip(),
                priority=milestone.priority,
                function_ids=_clean(milestone.function_ids),
                tasks=tasks,
            )
        )
    risks = [
        Risk(risk=row.risk.strip(), mitigation=row.mitigation.strip())
        for row in model.risks
        if row.risk.strip() or row.mitigation.strip()
    ]
    return PlanModel(
        crosscutting=crosscutting,
        milestones=milestones,
        environment=model.environment.strip(),
        risks=risks,
    )
