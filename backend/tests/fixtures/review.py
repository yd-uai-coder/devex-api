"""設計レビューの構造化出力の見本(レビューのテストで偽の LLM に返させる)。"""

from app.review import ReviewFindingOut, ReviewOutput


def review_finding(
    *,
    level: str = "major",
    cause: str = "design",
    point_key: str | None = None,
    problem: str = "API に認可が無い",
    documents: tuple[str, ...] = ("external_design",),
) -> ReviewFindingOut:
    """指摘1件(重要度・原因・観点のキー・問題・関連する文書を変えられる)。"""
    return ReviewFindingOut(
        level=level,  # type: ignore[arg-type]
        cause=cause,  # type: ignore[arg-type]
        targets=["POST /api/v1/projects"],
        documents=list(documents),
        point_key=point_key,
        problem=problem,
        evidence="POST /api/v1/projects … 200 OK",
        reason="他人のプロジェクトを操作できる",
        suggestion="持ち主の確認を足す",
    )


def review_output() -> ReviewOutput:
    """軽微 → 最重要 → 中程度(ヒアリング)の順の3件(保存と表示で重要度の順に並ぶかを見る)。"""
    return ReviewOutput(
        findings=[
            review_finding(level="minor", problem="画面の名前が揃っていない"),
            review_finding(level="critical", problem="チャットの API でプロジェクトを特定できない"),
            review_finding(
                level="major",
                cause="hearing",
                point_key="security",
                problem="誰が誰のデータを見られるかが決まっていない",
                documents=("requirements", "external_design"),
            ),
        ]
    )
