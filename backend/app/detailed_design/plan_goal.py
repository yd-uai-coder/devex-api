"""マイルストーンのゴールと、その中のタスクの突き合わせ(純粋関数)。

ゴールは AI が自由文で書くので、ゴールが約束するもの(CI/CD・バックアップなど)を作るタスクが
同じマイルストーンに無いことがある(ゴールは CI/CD を挙げ、タスクは Docker と nginx だけ、など)。
ゴールの文は自由なので、すべての食い違いは見られない。誤って指摘しにくい、少数の語だけを表で
照らす(`GOAL_TERMS`)。証拠は、そのマイルストーンのタスクの名前・モジュール・環境・設定のファイル。
フロントエンドは、機能のタスク(`feature`)があれば作るとみなす(計画の規則で、機能のタスクはその
処理のフロントエンドを含むため。簡易モードのモジュール一覧はバックエンドだけのことがある)。

実装計画書(簡易モード)と段階7(詳細設計モード)の検証が使う。どちらも警告で、承認は止めない。
"""

import re
from dataclasses import dataclass

from app.detailed_design.plan import Milestone


@dataclass(frozen=True)
class GoalTerm:
    """ゴールに出る語(`pattern`)と、それを作るタスクの証拠(小文字で照らす部分文字列)。
    `feature_covers`は、機能のタスクがあれば作るとみなすか。"""

    label: str
    pattern: re.Pattern[str]
    evidence: tuple[str, ...]
    feature_covers: bool = False


GOAL_TERMS: tuple[GoalTerm, ...] = (
    GoalTerm(
        "CI/CD",
        re.compile(r"CI/CD|(?<![A-Za-z])CI(?![A-Za-z])|継続的インテグレーション|継続的デリバリ"),
        (".github/workflows", "gitlab-ci", "ci.yml", "ci/", "ci/cd", "継続的"),
    ),
    GoalTerm("バックアップ", re.compile(r"バックアップ"), ("backup", "バックアップ", "pg_dump")),
    GoalTerm(
        "フロントエンド",
        re.compile(r"Next\.js|フロントエンド|React"),
        ("frontend", "next", ".tsx", "package.json", "フロントエンド", "react"),
        feature_covers=True,
    ),
    GoalTerm(
        "監視",
        re.compile(r"監視|モニタリング"),
        ("monitor", "prometheus", "grafana", "監視", "モニタリング"),
    ),
)

# 計画のプロンプトの規則(実装計画書・段階7で共有する)
GOAL_RULE = (
    "- マイルストーンのゴールには、そのマイルストーンのタスクで作るものだけを書く。"
    "CI/CD・バックアップ・フロントエンド・監視などをゴールに書くなら、それを作るタスクを同じ"
    "マイルストーンに置き、そのファイルを modules か config_files に書く"
    "(作らないものはゴールに書かない)\n"
)


def uncovered_goal_terms(milestone: Milestone) -> list[str]:
    """ゴールに出る語のうち、そのマイルストーンのどのタスクにも証拠が無いもの(表の順)。"""
    evidence = " ".join(
        [t.title for t in milestone.tasks]
        + [m for t in milestone.tasks for m in t.modules]
        + [c for t in milestone.tasks for c in t.config_files]
    ).lower()
    has_feature = any(t.kind == "feature" for t in milestone.tasks)
    return [
        term.label
        for term in GOAL_TERMS
        if term.pattern.search(milestone.goal)
        and not (term.feature_covers and has_feature)
        and not any(word in evidence for word in term.evidence)
    ]
