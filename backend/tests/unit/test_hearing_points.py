"""ヒアリングの観点の表(観点の定義・必須・完了の判定・判定の取り込み・まとめ)のテスト。

SUT: app/hearing/points.py と、パッケージの re-export(app/hearing/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。
"""

from app.hearing import (
    POINT_DEFS,
    POINT_KEYS,
    JudgedPoint,
    PointState,
    build_summary,
    describe_points,
    effective_scale,
    initial_states,
    is_required,
    is_sufficient,
    merge_judgement,
    missing_labels,
    number_messages,
    open_optional,
    open_required,
    questions_for_reply,
    scale_after,
    to_state,
)


def _decide_all(states: dict[str, PointState], *, except_keys: tuple[str, ...] = ()) -> None:
    for key in list(states):
        if key not in except_keys:
            states[key] = PointState(states[key].key, "decided", f"{key}の決定", ("m1",))


def test_point_defs_cover_function_and_nonfunctional_points_in_order() -> None:
    """機能面10 + IPA の6大項目。規模を最初に聞く(定義の順 = 画面の順)。"""
    assert [d.key for d in POINT_DEFS] == list(POINT_KEYS)
    assert POINT_DEFS[0].key == "scale"
    assert sum(1 for d in POINT_DEFS if d.kind == "function") == 10
    assert sum(1 for d in POINT_DEFS if d.kind == "nonfunctional") == 6


def test_required_points_switch_by_scale() -> None:
    assert len(open_required(initial_states(), "personal")) == 10
    assert len(open_required(initial_states(), "internal")) == 14
    assert len(open_required(initial_states(), "public")) == 15
    # 技術スタックは規模によらず必須、体制・期間はどの規模でも任意
    assert all(is_required("tech", s) for s in ("personal", "internal", "public"))
    assert not any(is_required("schedule", s) for s in ("personal", "internal", "public"))


def test_undecided_scale_counts_with_personal_column() -> None:
    assert effective_scale(None) == "personal"
    assert [d.key for d in open_required(initial_states(), None)] == [
        d.key for d in open_required(initial_states(), "personal")
    ]


def test_sufficient_when_every_required_point_is_not_open() -> None:
    states = initial_states()
    _decide_all(states, except_keys=("daily_flow", "future", "schedule"))
    assert is_sufficient(states, "personal")
    # 社内なら「利用者の1日の流れ」も必須
    assert not is_sufficient(states, "internal")
    assert missing_labels(states, "internal") == ["利用者の1日の流れ"]


def test_not_applicable_counts_as_settled() -> None:
    states = initial_states()
    _decide_all(states, except_keys=("migration",))
    states["migration"] = PointState("migration", "not_applicable", "新規", ("m2",))
    assert is_sufficient(states, "public")


def test_questions_for_reply_takes_open_required_in_order_up_to_limit() -> None:
    questions = questions_for_reply(initial_states(), None)

    assert len(questions) == 3
    assert questions[0].startswith("規模(例: ")
    assert questions[1].startswith("目的・課題")


def test_merge_takes_decisions_with_evidence_and_keeps_others() -> None:
    previous = initial_states()
    previous["purpose"] = PointState("purpose", "decided", "前の目的", ("m1",))

    merged = merge_judgement(
        previous,
        [
            JudgedPoint("users", "decided", " 倉庫の担当者 ", ("m2",)),
            JudgedPoint("security", "adopted", "本人だけ", ("m3",)),
        ],
        user_messages={"m2", "m3"},
    )

    assert merged["purpose"] == previous["purpose"]  # 返らなかった観点は前のまま
    assert merged["users"] == PointState("users", "decided", "倉庫の担当者", ("m2",))
    assert merged["security"].status == "adopted"


def test_merge_ignores_decisions_without_evidence() -> None:
    """根拠の発言が無い決定は取り込まない(AI の推測を決定と数えない)。"""
    merged = merge_judgement(
        initial_states(), [JudgedPoint("tech", "decided", "Next.js", ())], user_messages=set()
    )

    assert merged["tech"].status == "open"


def test_merge_takes_adoption_and_scale_only_from_user_messages() -> None:
    """初期入力(intake)だけを根拠にした採用と、規模・技術スタックは取り込まない。ほかの観点の
    決定は初期入力からでもよい。"""
    judged = [
        JudgedPoint("tech", "decided", "PostgreSQL", ("intake",)),
        JudgedPoint("scale", "decided", "社内", ("intake",)),
        JudgedPoint("core", "decided", "一覧と登録", ("intake",)),
        JudgedPoint("security", "adopted", "本人だけ", ("intake", "m2")),
    ]

    merged = merge_judgement(initial_states(), judged, user_messages={"m2"})

    assert merged["tech"].status == "open"
    assert merged["scale"].status == "open"
    assert merged["core"].status == "decided"
    assert merged["security"].status == "adopted"


def test_only_scale_and_tech_are_confirmed_in_chat() -> None:
    assert [d.key for d in POINT_DEFS if d.confirm_in_chat] == ["scale", "tech"]


def test_merge_accepts_reopening_without_evidence() -> None:
    previous = initial_states()
    previous["scope"] = PointState("scope", "decided", "一覧だけ", ("m1",))

    merged = merge_judgement(
        previous, [JudgedPoint("scope", "open", "", ())], user_messages=set()
    )

    assert merged["scope"].status == "open"


def test_merge_ignores_unknown_keys_and_statuses() -> None:
    merged = merge_judgement(
        initial_states(),
        [JudgedPoint("budget", "decided", "x", ("m1",)), JudgedPoint("core", "done", "x", ("m1",))],
        user_messages={"m1"},
    )

    assert merged == initial_states()
    assert to_state("budget", "decided", "x") is None


def test_scale_after_follows_scale_point_state() -> None:
    states = initial_states()
    assert scale_after(states, "internal", "public") is None  # 規模が未決
    states["scale"] = PointState("scale", "decided", "社内", ("m1",))
    assert scale_after(states, None, "internal") == "internal"
    assert scale_after(states, "internal", None) == "internal"  # 判定が規模を返さなければ前の値


def test_build_summary_lists_settled_points_in_order() -> None:
    states = initial_states()
    states["purpose"] = PointState("purpose", "decided", "工数を減らす", ("m1",))
    states["migration"] = PointState("migration", "not_applicable", "新規", ("m2",))

    summary = build_summary(states, "internal")

    assert summary.splitlines() == [
        "- 規模: 社内",
        "- 目的・課題: 工数を減らす",
        "- 移行性: 対象外(新規)",
    ]


def test_number_messages_numbers_user_side_messages_and_labels_them() -> None:
    numbered = number_messages(
        [("i", "intake"), ("a", "attachment"), ("x", "ai"), ("u1", "user"), ("u2", "user")]
    )

    assert numbered == [
        ("i", 1, "初期入力"),
        ("a", 2, "添付資料"),
        ("u1", 3, "発言1"),
        ("u2", 4, "発言2"),
    ]


def test_open_optional_lists_open_points_not_required_for_scale() -> None:
    states = initial_states()
    states["schedule"] = PointState("schedule", "decided", "1人", ("m",))

    personal = [d.key for d in open_optional(states, "personal")]
    assert personal == ["daily_flow", "future", "operability", "migration", "environment"]
    assert [d.key for d in open_optional(states, "public")] == []
    assert describe_points(open_optional(states, "internal")) == [
        "将来の拡張(例: 後で足したいと思っているものはありますか)"
    ]

