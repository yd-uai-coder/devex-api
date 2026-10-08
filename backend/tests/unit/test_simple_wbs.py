"""簡易モードの実装計画書の WBS の解析(純粋関数)のテスト。

SUT は`app/detailed_design/simple_procedure/wbs.py`の`parse_wbs`、ドライバはこのテスト。
スタブ不要 ── 対象は実装計画書の Markdown の文字列だけから決まり、DB や LLM を呼ばないため。
"""

from tests.fixtures.simple_procedure import OLD_PLAN_MD, PLAN_MD

from app.detailed_design.plan import unit_ids
from app.detailed_design.simple_procedure import WbsParse, parse_wbs


def _codes(result: WbsParse) -> list[str]:
    return [issue.code for issue in result.issues]


def test_parses_milestones_and_units_in_order() -> None:
    """統合スモーク: 書式どおりの WBS は指摘0件で、段階7と同じ形に読める。"""
    result = parse_wbs(PLAN_MD)

    assert result.issues == ()
    plan = result.plan
    assert [(m.name, m.priority, m.goal) for m in plan.milestones] == [
        ("予約の登録", "Must", "備品を予約できる"),
        ("予約の一覧", "Should", "自分の予約を一覧で確かめられる"),
    ]
    assert unit_ids(plan) == ["M-01-T01", "M-01-T02", "M-02-T01"]
    base, feature = plan.milestones[0].tasks
    assert (base.kind, base.function_ids, base.depends_on) == ("base", [], [])
    assert base.config_files == ["docker-compose.yml", ".env.example"]
    assert (feature.kind, feature.title) == ("feature", "予約を登録する")
    assert feature.function_ids == ["DF-1"]
    assert feature.depends_on == ["M-01-T01"]
    # バッククォートは外す
    assert feature.modules == ["app/api/reservations.py", "app/services/reservation.py"]
    assert plan.environment == "- Docker Compose で API と DB を起動する"


def test_old_format_asks_to_regenerate() -> None:
    """番号の無い旧形式は単位0件で、最重要の WBS_MISSING(再生成)になる。"""
    result = parse_wbs(OLD_PLAN_MD)

    assert unit_ids(result.plan) == []
    assert _codes(result) == ["WBS_MISSING"]  # 行ごとの指摘は出さない
    first = result.issues[0]
    assert (first.code, first.level, first.fix_document) == (
        "WBS_MISSING",
        "critical",
        "implementation_plan",
    )
    assert "古い形式" in first.message


def test_missing_section() -> None:
    result = parse_wbs("# 4. 実装計画書\n\n## 4.1 開発フェーズ\n- なし\n")

    assert _codes(result) == ["WBS_MISSING"]


def test_written_id_differs_from_order() -> None:
    """書かれた ID が並び順と違えば軽微の指摘にし、依存先は導いた ID に読み替える。"""
    markdown = (
        "## 4.2 タスク分解\n### M-01: 準備 ── 【Must】\n"
        "- [ ] M-01-T05 [基盤] 準備する\n"
        "- [ ] M-01-T09 [機能] 登録する\n  - 処理: DF-1\n  - 依存: M-01-T05\n"
    )

    result = parse_wbs(markdown)

    assert _codes(result) == ["WBS_ID_MISMATCH", "WBS_ID_MISMATCH"]
    assert result.issues[1].unit == "M-01-T02"
    assert result.plan.milestones[0].tasks[1].depends_on == ["M-01-T01"]


def test_broken_lines_are_reported() -> None:
    """読めない行・優先度の無い見出し・見出しの前のタスクは指摘にする(読める行は残す)。"""
    markdown = (
        "## 4.2 タスク分解\n"
        "- [ ] M-01-T01 [機能] 見出しの前\n"
        "### M-01: 登録\n"
        "- [ ] 番号の無いタスク\n"
        "- [ ] M-01-T01 [機能] 登録する\n  - 処理: DF-1\n"
        "### 補足\n"
    )

    result = parse_wbs(markdown)

    assert _codes(result) == ["WBS_FORMAT"] * 4
    assert result.plan.milestones[0].priority == "Must"
    assert unit_ids(result.plan) == ["M-01-T01"]


def test_wont_milestone_is_skipped() -> None:
    markdown = (
        "## 4.2 タスク分解\n### M-01: 登録 ── 【Must】\n- [ ] M-01-T01 [機能] 登録する\n"
        "### M-02: 通知 ── 【Won't】\n- [ ] M-02-T01 [機能] 通知する\n"
    )

    result = parse_wbs(markdown)

    assert unit_ids(result.plan) == ["M-01-T01"]
    assert [(i.code, i.level) for i in result.issues] == [("WBS_FORMAT", "minor")]
