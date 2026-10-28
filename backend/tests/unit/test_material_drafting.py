"""違いから CR の下書きへの組み立て(純粋関数)のテスト。

SUT: app/materials/drafting.py の requirement_links・requirement_candidates・origin_keys・
     draft_change_request(パッケージ app.materials の re-export から)
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で、DB にも LLM にも触らないため。束の写しは dict で直接渡す
"""

from datetime import date

import pytest
from tests.fixtures.change_impact import impact_documents

from app.change_requests.targets import Target
from app.materials import (
    BACKGROUND_SOURCE_OF_MATERIAL,
    DRAFTABLE_KINDS,
    ChangeRequestDraft,
    DraftDifference,
    DraftMaterial,
    draft_change_request,
    origin_keys,
    requirement_candidates,
    requirement_links,
)
from app.materials.drafting import CONTENT_MAX, TITLE_MAX

_MATERIAL = DraftMaterial("minutes", "第3回 定例", date(2026, 10, 20))
_API = "api:POST /api/v1/reservations"


def _links() -> dict[str, tuple[str, ...]]:
    docs = impact_documents()
    docs["requirements"]["users"] = [{"user": "利用者", "scene": "席を予約する", "source": "user"}]
    docs["external_design"]["integrations"] = [
        {"service": "SendGrid", "purpose": "メール", "req_ids": ["FR-001", "FR-999"]},
        {"service": "Slack", "purpose": "通知"},
    ]
    docs["external_design"]["screens"].append({"id": "SCR-009", "name": "お知らせ"})
    return requirement_links(docs)


def _diff(
    kind: str, target: str = "", origin: str = "", content: str = "変える"
) -> DraftDifference:
    quote = "根拠になる資料の文です"
    return DraftDifference(kind, target, origin, content, quote)  # type: ignore[arg-type]


def test_links_follow_rows_and_drop_unknown_requirements() -> None:
    links = _links()

    assert links["req:FR-001"] == ("FR-001",)
    assert links["screen:SCR-001"] == ("FR-001", "FR-002")
    assert links[_API] == ("FR-001",)
    assert links["integration:SendGrid"] == ("FR-001",)  # 束に無い FR-999 は除く
    assert links["integration:Slack"] == ()
    assert links["users"] == ()


def test_candidates_only_for_change_and_delete() -> None:
    links = _links()

    assert requirement_candidates("change", "screen:SCR-001", links) == ("FR-001", "FR-002")
    assert requirement_candidates("delete", "req:FR-002", links) == ("FR-002",)
    assert requirement_candidates("add", "", links) == ()
    assert requirement_candidates("question", "req:FR-001", links) == ()
    assert set(DRAFTABLE_KINDS) == {"add", "change", "delete"}


def test_origin_keys_map_integrations_and_users() -> None:
    links = _links()

    assert origin_keys("screen:SCR-001", links) == ("screen:SCR-001",)
    assert origin_keys(_API, links) == (_API,)
    assert origin_keys("integration:SendGrid", links) == ("req:FR-001",)
    assert origin_keys("users", links) == ()
    assert origin_keys("", links) == ()
    assert origin_keys("screen:SCR-404", links) == ()


def test_requirement_differences_become_targets() -> None:
    draft = draft_change_request(
        [
            _diff("change", "req:FR-001", content="登録の後にメールを送る"),
            _diff("delete", "req:NFR-001"),
        ],
        _MATERIAL,
        _links(),
    )

    assert isinstance(draft, ChangeRequestDraft)
    assert draft.targets == (Target("FR-001"), Target("NFR-001", "delete"))
    assert not draft.adds_requirement
    assert draft.origins == ()


def test_element_differences_take_all_candidates_or_chosen() -> None:
    differences = [_diff("change", "screen:SCR-001"), _diff("delete", _API)]

    every = draft_change_request(differences, _MATERIAL, _links())
    chosen = draft_change_request(differences, _MATERIAL, _links(), ["FR-002"])

    # 要素の削除は要件の内容の変更になる
    assert every.targets == (Target("FR-001"), Target("FR-002"))
    assert chosen.targets == (Target("FR-002"),)


def test_choosing_a_requirement_outside_candidates_is_rejected() -> None:
    with pytest.raises(ValueError, match="NFR-001"):
        draft_change_request([_diff("change", "screen:SCR-001")], _MATERIAL, _links(), ["NFR-001"])


def test_additions_carry_origins() -> None:
    draft = draft_change_request(
        [
            _diff("add", origin="screen:SCR-001"),
            _diff("add", origin="integration:SendGrid"),
            _diff("add", origin="users"),
            _diff("add", origin="screen:SCR-001"),
        ],
        _MATERIAL,
        _links(),
    )

    assert draft.targets == ()
    assert draft.adds_requirement
    assert draft.origins == ("screen:SCR-001", "req:FR-001")


def test_element_without_requirements_becomes_new_requirement() -> None:
    draft = draft_change_request(
        [_diff("change", "screen:SCR-009"), _diff("change", "users")], _MATERIAL, _links()
    )

    assert draft.targets == ()
    assert draft.adds_requirement
    assert draft.origins == ("screen:SCR-009",)


def test_questions_and_empty_selection_are_rejected() -> None:
    with pytest.raises(ValueError, match="確かめる点"):
        draft_change_request([_diff("question", "req:FR-001")], _MATERIAL, _links())
    with pytest.raises(ValueError, match="1件以上"):
        draft_change_request([], _MATERIAL, _links())


def test_conflicting_kinds_on_one_requirement_are_rejected() -> None:
    with pytest.raises(ValueError, match="違う種類"):
        draft_change_request(
            [_diff("delete", "req:FR-001"), _diff("change", "screen:SCR-001")],
            _MATERIAL,
            _links(),
        )


def test_title_content_and_background() -> None:
    one = draft_change_request(
        [_diff("change", "req:FR-001", content="一覧を\n日付順にする")], _MATERIAL, _links()
    )
    many = draft_change_request(
        [
            _diff("change", "req:FR-001"),
            _diff("add", origin="screen:SCR-001", content="要約を足す"),
        ],
        DraftMaterial("email", "外部向けの API", date(2026, 10, 21)),
        _links(),
    )

    assert one.title == "一覧を 日付順にする"
    assert one.background_source == "meeting"
    assert one.background_date == date(2026, 10, 20)
    assert one.background_memo == "資料「第3回 定例」(2026-10-20)の違い1件から作った下書き"
    assert many.title == "「外部向けの API」の変更(2件)"
    assert many.background_source == BACKGROUND_SOURCE_OF_MATERIAL["email"] == "email"
    assert "- 変更 req:FR-001: 変える" in many.content
    assert "- 追加 新規(起点 screen:SCR-001): 要約を足す" in many.content
    assert "  - 根拠: 「根拠になる資料の文です」" in many.content


def test_long_texts_are_cut_to_limits() -> None:
    long = DraftDifference("change", "req:FR-001", "", "長" * 1000, "引" * 1000)
    draft = draft_change_request([long] * 30, _MATERIAL, _links())
    single = draft_change_request([long], _MATERIAL, _links())

    assert len(single.title) == TITLE_MAX and single.title.endswith("…")
    assert len(draft.content) <= CONTENT_MAX
    # 先に引用を切り詰める
    assert "引" * 199 + "…" in draft.content
