"""マイルストーンのゴールとタスクの突き合わせ(純粋関数)。

SUT: app/detailed_design/plan_goal.uncovered_goal_terms と、プロンプトの規則 GOAL_RULE。
ドライバ: このテスト。スタブ不要 ── 対象が純粋(副作用なし)で、LLM・DB を呼ばないため。
"""

from app.detailed_design.plan import Milestone, PlanTask
from app.detailed_design.plan_drafting import PLAN_SYSTEM_PROMPT
from app.detailed_design.plan_goal import GOAL_RULE, GOAL_TERMS, uncovered_goal_terms
from app.documents.implementation_plan.drafting import SYSTEM_PROMPT


def _milestone(goal: str, *tasks: PlanTask) -> Milestone:
    return Milestone(name="基盤", goal=goal, tasks=list(tasks))


_DOCKER = PlanTask(
    kind="base",
    title="Docker環境の構築",
    config_files=["Dockerfile", "docker-compose.yml", "nginx/conf.d/default.conf"],
)


def test_smoke_reports_goal_terms_without_tasks():
    # 実際に出た形(ゴールは CI/CD・バックアップを挙げ、タスクは Docker と nginx だけ)
    milestone = _milestone(
        "コンテナ環境、データベース、Redis、リポジトリおよびCI/CD・バックアップ基盤が整うこと。",
        _DOCKER,
    )
    assert uncovered_goal_terms(milestone) == ["CI/CD", "バックアップ"]


def test_frontend_goal_needs_a_frontend_file_or_title():
    goal = "FastAPIとNext.jsの基本設定が完了する"
    assert uncovered_goal_terms(_milestone(goal, _DOCKER)) == ["フロントエンド"]
    by_file = PlanTask(kind="base", title="雛形", config_files=["frontend/package.json"])
    by_title = PlanTask(kind="base", title="Next.js の雛形を作る")
    assert uncovered_goal_terms(_milestone(goal, _DOCKER, by_file)) == []
    assert uncovered_goal_terms(_milestone(goal, _DOCKER, by_title)) == []
    # 機能のタスクはその処理のフロントエンドを含む(計画の規則)ので、フロントエンドの証拠になる
    feature = PlanTask(kind="feature", title="プレビュー", modules=["backend/api/projects.py"])
    preview = "フロントエンドでプレビューできる"
    assert uncovered_goal_terms(_milestone(preview, feature)) == []


def test_evidence_comes_from_modules_config_files_and_titles():
    ci = PlanTask(kind="base", title="CI", config_files=[".github/workflows/ci.yml"])
    backup = PlanTask(kind="base", title="定期実行", modules=["scripts/backup.py"])
    monitor = PlanTask(kind="base", title="監視の設定")
    goal = "CI/CD とバックアップと監視が動く"
    assert uncovered_goal_terms(_milestone(goal, ci, backup, monitor)) == []


def test_terms_without_goal_words_are_not_reported():
    # CI はほかの英字に挟まれた語(CIRCLE など)では語とみなさない。機能のゴールは照らさない
    assert uncovered_goal_terms(_milestone("CIRCLE の設定と予約の登録ができる", _DOCKER)) == []
    assert uncovered_goal_terms(_milestone("プレビューの切り替え表示ができる")) == []
    assert [t.label for t in GOAL_TERMS] == ["CI/CD", "バックアップ", "フロントエンド", "監視"]


def test_both_plan_prompts_carry_the_goal_rule():
    assert "そのマイルストーンのタスクで作るものだけ" in GOAL_RULE
    assert GOAL_RULE in PLAN_SYSTEM_PROMPT
    assert GOAL_RULE in SYSTEM_PROMPT
