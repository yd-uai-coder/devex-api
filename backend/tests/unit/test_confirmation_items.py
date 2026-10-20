"""確認事項の一覧の項目と、推測の採用率(純粋関数)。

SUT: app.confirmations の confirmation_items / adoption_counts / 項目とキーの関数、
     app.documents.requirements の grounding_points / mark_user_adopted
ドライバ: 各テスト関数
スタブ不要 ── 対象が純粋(副作用なし)で、LLM・DB を呼ばないため。観点の表は PointState を
直接作って渡す。
"""

from app.confirmations import (
    AdoptionCounts,
    ConfirmationItem,
    adoption_counts,
    confirmation_items,
    confirmation_kind_of,
    item_target,
    point_item_key,
    requirement_item,
    requirement_item_key,
    user_item,
    user_item_key,
)
from app.documents.requirements import (
    USER_POINTS,
    RequirementRow,
    RequirementsModel,
    UserScene,
    grounding_points,
    mark_user_adopted,
)
from app.hearing import PointState, initial_states


def _states(**statuses: str) -> dict[str, PointState]:
    states = initial_states()
    for key, status in statuses.items():
        states[key] = PointState(key, status, f"{key}の決定")  # type: ignore[arg-type]
    return states


def _model() -> RequirementsModel:
    return RequirementsModel(
        purpose="目的",
        users=[
            UserScene(user="担当者", scene="毎朝確認する", source="user"),
            UserScene(user="管理者", scene="月末に集計する", source="assumed"),
        ],
        rows=[
            RequirementRow(
                id="FR-001", kind="FR", priority="Must", text="記事を集める",
                source="user", points=["core"],
            ),
            RequirementRow(
                id="FR-002", kind="FR", priority="Should", text="通知する",
                source="assumed", points=["core", "scale"],
            ),
            RequirementRow(
                id="CON-001", kind="CON", category="規模", text="社内で使う",
                source="assumed", points=["scale"],
            ),
        ],
    )


def test_keys_round_trip() -> None:
    assert requirement_item_key("FR-001") == "req:FR-001"
    assert user_item_key(" 管理者 ") == "user:管理者"
    assert point_item_key("core") == "point:core"
    assert confirmation_kind_of("req:FR-001") == "requirement"
    assert confirmation_kind_of("user:管理者") == "user"
    assert confirmation_kind_of("point:core") == "point"
    assert confirmation_kind_of("screen:SCR-001") is None
    assert confirmation_kind_of("req:") is None
    assert item_target("user:管理者") == "管理者"
    model = _model()
    assert requirement_item(model.rows[1]).label == "FR-002 通知する"
    expected = ConfirmationItem("user:管理者", "user", "管理者: 月末に集計する")
    assert user_item(model.users[1]) == expected


def test_grounding_points_excludes_scale_except_constraints() -> None:
    assert grounding_points(["core", "scale"], "FR") == ["core"]
    assert grounding_points(["scale"], "CON") == ["scale"]
    assert USER_POINTS == ("users", "daily_flow")


def test_items_collect_assumed_rows_users_and_open_points_in_order() -> None:
    """推測の要件(文書の順)→ 推測の利用者 → 根拠が未決の観点(観点の表の順)。"""
    items = confirmation_items(_model(), _states(core="decided"))
    assert [i.key for i in items] == [
        "req:FR-002",
        "req:CON-001",
        "user:管理者",
        "point:scale",
        "point:users",
        "point:daily_flow",
    ]
    assert items[0] == ConfirmationItem("req:FR-002", "requirement", "FR-002 通知する")
    assert items[2].label == "管理者: 月末に集計する"
    # 規模は制約の行だけ根拠に数える(FR-002 の scale は数えない)
    assert items[3].refs == ("CON-001",)
    assert items[4].refs == ("1.2",)


def test_items_list_open_point_with_all_relying_rows() -> None:
    states = _states(scale="decided", users="adopted", daily_flow="not_applicable")
    items = confirmation_items(_model(), states)
    points = [i for i in items if i.kind == "point"]
    assert [(i.key, i.refs) for i in points] == [("point:core", ("FR-001", "FR-002"))]


def test_items_treat_missing_state_as_open_and_empty_without_model() -> None:
    model = RequirementsModel(
        purpose="目的",
        rows=[RequirementRow(id="FR-001", kind="FR", text="a", source="user", points=["core"])],
    )
    assert [i.key for i in confirmation_items(model, {})] == ["point:core"]
    assert confirmation_items(None, _states()) == []


def test_mark_user_adopted_changes_only_assumed_user() -> None:
    adopted = mark_user_adopted(_model(), "管理者")
    assert adopted is not None
    assert [u.source for u in adopted.users] == ["user", "adopted"]
    assert mark_user_adopted(_model(), "担当者") is None  # 推測でない行は変えない
    assert mark_user_adopted(_model(), "誰か") is None


def test_adoption_counts_rate() -> None:
    counts = adoption_counts(
        [
            ("requirement", "confirmed", False),  # 採用にして一覧から消えた行も数える
            ("user", "rejected", True),
            ("requirement", "confirmed", True),
            ("requirement", "unconfirmed", True),
            ("requirement", "unconfirmed", False),  # 一覧から消えた未確認は数えない
            ("point", "confirmed", True),  # 観点は数えない
        ]
    )
    assert counts == AdoptionCounts(confirmed=2, rejected=1, unconfirmed=1)
    assert counts.rate == 2 / 3
    assert AdoptionCounts(0, 0, 3).rate is None
