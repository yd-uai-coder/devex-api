"""確認事項の一覧の項目と、推測の採用率(純粋関数)。

SUT: app.confirmations の confirmation_items / adoption_counts / 項目とキーの関数 /
     legacy_user_keys、app.documents.requirements の grounding_points / mark_user_adopted /
     利用者の ID(UserScene.id・number_users・assign_user_ids・build_model の利用者)、
     言い換えた再生成の出力(app/ai/llm/paraphrase.py の paraphrase_requirements)
ドライバ: 各テスト関数
スタブ不要 ── 対象が純粋(副作用なし)で、LLM・DB を呼ばないため。観点の表は PointState を
直接作って渡す。
"""

from tests.fixtures.requirements import requirements_output

from app.ai.llm.paraphrase import paraphrase_requirements
from app.confirmations import (
    AdoptionCounts,
    ConfirmationItem,
    adoption_counts,
    confirmation_items,
    confirmation_kind_of,
    item_target,
    legacy_user_keys,
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
    UserSceneDraft,
    assign_user_ids,
    build_model,
    grounding_points,
    mark_user_adopted,
    number_users,
)
from app.documents.requirements.drafting import (
    GeneratedUserScene,
    RequirementsGenerationOutput,
    to_draft,
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
    assert user_item_key(" U-002 ") == "user:U-002"
    assert point_item_key("core") == "point:core"
    assert confirmation_kind_of("req:FR-001") == "requirement"
    assert confirmation_kind_of("user:U-002") == "user"
    assert confirmation_kind_of("point:core") == "point"
    assert confirmation_kind_of("screen:SCR-001") is None
    assert confirmation_kind_of("req:") is None
    assert item_target("user:U-002") == "U-002"
    model = _model()
    assert requirement_item(model.rows[1]).label == "FR-002 通知する"
    expected = ConfirmationItem("user:U-002", "user", "管理者: 月末に集計する")
    assert user_item(model.users[1]) == expected


def test_users_without_ids_are_numbered_in_order() -> None:
    """ID を振る前に保存した版は、読むたびに並び順で同じ ID になる。次の番号も越える。"""
    model = _model()
    assert [u.id for u in model.users] == ["U-001", "U-002"]
    assert model.next_seq["U"] == 3
    stored = _model().model_dump(mode="json")
    del stored["next_seq"]["U"]
    for user in stored["users"]:
        del user["id"]
    assert [u.id for u in RequirementsModel.model_validate(stored).users] == ["U-001", "U-002"]


def test_number_users_carries_ids_by_name_from_known_rows() -> None:
    known = [UserScene(id="U-005", user="管理者", scene="月末", source="assumed")]
    users, next_seq = number_users(
        [
            UserScene(user="管理者", scene="月末", source="assumed"),
            UserScene(user="新しい人", scene="朝", source="assumed"),
        ],
        {"U": 2},
        known,
    )
    assert [u.id for u in users] == ["U-005", "U-006"]
    assert next_seq["U"] == 7


def test_assign_user_ids_by_prev_id_then_name_then_scene() -> None:
    previous = _model()  # U-001 担当者(毎朝確認する)・U-002 管理者(月末に集計する)
    ids, next_seq = assign_user_ids(
        [
            UserSceneDraft("責任者", "月末に集計する", "assumed"),  # 場面で U-002
            UserSceneDraft("担当の人", "朝", "user", prev_id="u-001"),  # prev_id
            UserSceneDraft("来訪者", "受付で", "assumed"),  # 新しい
        ],
        previous,
    )
    assert ids == ["U-002", "U-001", "U-003"]
    assert next_seq == 4


def test_assign_user_ids_does_not_guess_between_two_candidates() -> None:
    previous = RequirementsModel(
        purpose="目的",
        users=[
            UserScene(user="A", scene="同じ場面", source="user"),
            UserScene(user="B", scene="同じ場面", source="user"),
        ],
    )
    ids, _ = assign_user_ids([UserSceneDraft("C", "同じ場面", "user")], previous)
    assert ids == ["U-003"]


def test_paraphrased_user_keeps_the_same_item_key() -> None:
    """再生成で LLM が推測の利用者の名前を言い換えても(prev_id を書かなくても)、場面で同じ ID を
    引き継ぎ、確認事項の項目のキーは変わらない。"""
    states = _states(core="decided")
    first = build_model(to_draft(_users_output()), None, states, None)
    second = build_model(to_draft(paraphrase_requirements(_users_output())), first, states, None)

    assert [u.user for u in second.users] != [u.user for u in first.users]
    first_keys = [i.key for i in confirmation_items(first, states) if i.kind == "user"]
    second_keys = [i.key for i in confirmation_items(second, states) if i.kind == "user"]
    assert first_keys == second_keys == ["user:U-001", "user:U-002"]


def _users_output() -> RequirementsGenerationOutput:
    output = requirements_output()
    output.users = [
        GeneratedUserScene(user="担当者", scene="毎朝確認する", source="user"),
        GeneratedUserScene(user="管理者", scene="月末に集計する", source="assumed"),
    ]
    return output


def test_legacy_user_keys_map_names_to_id_keys() -> None:
    assert legacy_user_keys(_model()) == {"user:担当者": "user:U-001", "user:管理者": "user:U-002"}
    assert legacy_user_keys(None) == {}


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
        "user:U-002",
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
    adopted = mark_user_adopted(_model(), "U-002")
    assert adopted is not None
    assert [u.source for u in adopted.users] == ["user", "adopted"]
    assert mark_user_adopted(_model(), "U-001") is None  # 推測でない行は変えない
    assert mark_user_adopted(_model(), "U-009") is None


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
