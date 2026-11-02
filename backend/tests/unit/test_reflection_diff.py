"""CR の反映の前 → 後(純粋関数)のテスト。

SUT: reflection_changes・changes_to_json・changes_from_json・applied_reflections
     (app/agreements/reflection_diff.py)
ドライバ: 各テスト関数
スタブ: スタブ不要 ── 対象が純粋(副作用なし)で、DB にも LLM にも触らないため。要素は
agreement_items で本物の model から作る
"""

from datetime import UTC, datetime

from app.agreements import (
    AppliedReflection,
    agreement_items,
    applied_reflections,
    changes_from_json,
    changes_to_json,
    reflection_changes,
)
from app.documents.external_design.model import ApiRow, ExternalDesignModel, ScreenRow
from app.documents.requirements.model import RequirementRow, RequirementsModel, UserScene

_AT = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)


def _req(req_id: str, text: str) -> RequirementRow:
    return RequirementRow(id=req_id, kind="FR", priority="Must", text=text, source="user")


def _items(
    rows: list[RequirementRow],
    screens: list[ScreenRow] | None = None,
    apis: list[ApiRow] | None = None,
    users: list[UserScene] | None = None,
):
    return agreement_items(
        RequirementsModel(purpose="", rows=rows, users=users or []),
        ExternalDesignModel(screens=screens or [], apis=apis or []),
    )


def _changes(before, after, **kwargs):
    options = {
        "target_keys": {"req:FR-001"},
        "rewritable": {"req:FR-001", "screen:SCR-001"},
        "adds_requirement": False,
    }
    return reflection_changes(before, after, **{**options, **kwargs})


def test_changed_target_has_field_before_and_after() -> None:
    before = _items([_req("FR-001", "予約を登録する"), _req("FR-002", "予約を消す")])
    after = _items([_req("FR-001", "予約を登録し、確認メールを送る"), _req("FR-002", "予約を消す")])

    changes = _changes(before, after)

    assert [(c.key, c.change, c.scope) for c in changes] == [("req:FR-001", "changed", "target")]
    [field] = changes[0].fields
    assert (field.name, field.label) == ("text", "内容")
    assert (field.before, field.after) == ("予約を登録する", "予約を登録し、確認メールを送る")


def test_unchanged_elements_are_left_out() -> None:
    items = _items([_req("FR-001", "予約を登録する")])

    assert _changes(items, items) == []


def test_scopes_split_target_predicted_and_outside() -> None:
    screen = ScreenRow(id="SCR-001", name="予約の登録", req_ids=["FR-001"])
    other = ScreenRow(id="SCR-002", name="予約の一覧", req_ids=["FR-002"])
    before = _items([_req("FR-001", "a")], screens=[screen, other])
    after = _items(
        [_req("FR-001", "a")],
        screens=[
            screen.model_copy(update={"role": "登録"}),
            other.model_copy(update={"req_ids": ["FR-002", "FR-001"]}),
        ],
    )

    changes = _changes(before, after)

    assert [(c.key, c.scope) for c in changes] == [
        ("screen:SCR-001", "predicted"),
        ("screen:SCR-002", "outside"),
    ]
    assert changes[1].fields[0].before == "FR-002"
    assert changes[1].fields[0].after == "FR-002、FR-001"


def test_added_and_removed_list_their_non_empty_fields() -> None:
    before = _items([_req("FR-001", "a"), _req("FR-002", "消す要件")])
    after = _items([_req("FR-001", "a"), _req("FR-003", "新しい要件")])

    changes = _changes(before, after, target_keys={"req:FR-002"}, adds_requirement=True)

    assert [(c.key, c.change, c.scope) for c in changes] == [
        ("req:FR-003", "added", "target"),  # CR が新しい要件を足す
        ("req:FR-002", "removed", "target"),
    ]
    added = {f.name: (f.before, f.after) for f in changes[0].fields}
    assert added["text"] == ("", "新しい要件")
    removed = {f.name: (f.before, f.after) for f in changes[1].fields}
    assert removed["text"] == ("消す要件", "")


def test_added_requirement_is_outside_when_cr_does_not_add() -> None:
    before = _items([_req("FR-001", "a")])
    after = _items([_req("FR-001", "a"), _req("FR-002", "勝手に足した")])

    [change] = _changes(before, after)

    assert (change.change, change.scope) == ("added", "outside")


def test_restored_elements_come_last_without_fields() -> None:
    before = _items([_req("FR-001", "a"), _req("FR-002", "b")])
    after = _items([_req("FR-001", "a2"), _req("FR-002", "b")])

    changes = _changes(
        before,
        after,
        protection=[{"key": "req:FR-002", "reason": "changed"}, {"key": "req:FR-999"}],
    )

    assert [(c.key, c.change) for c in changes] == [
        ("req:FR-001", "changed"),
        ("req:FR-002", "restored"),  # 知らないキーは飛ばす
    ]
    assert changes[1].fields == ()
    assert (changes[1].scope, changes[1].restore_reason) == ("outside", "changed")


def test_users_are_compared_as_one_element() -> None:
    before = _items([], users=[UserScene(id="U-001", user="店員", scene="受付", source="user")])
    after = _items([], users=[UserScene(id="U-001", user="店長", scene="受付", source="user")])

    [change] = _changes(before, after)

    assert (change.key, change.change) == ("users", "changed")
    assert (change.fields[0].before, change.fields[0].after) == ("店員: 受付", "店長: 受付")


def test_json_round_trip_and_broken_items_are_dropped() -> None:
    before = _items([_req("FR-001", "a")])
    after = _items([_req("FR-001", "b")])
    changes = _changes(before, after)

    saved = changes_to_json(changes)

    assert changes_from_json([*saved, {"key": "x"}, {"change": "?", "scope": "target"}]) == changes


def test_applied_reflections_are_newest_first_and_skip_stage_applies() -> None:
    before = _items([_req("FR-001", "a")])
    changes = changes_to_json(_changes(before, _items([_req("FR-001", "b")])))
    docs = [{"doc_type": "requirements", "version": 2}]
    later = datetime(2026, 10, 27, tzinfo=UTC)
    events = [
        ("create", _AT, None),
        ("apply", _AT, {"documents": docs, "model": "m"}),  # 前 → 後を残す前の反映
        ("apply", _AT, {"stage": 1, "version": 3}),  # 段階への反映
        ("apply", later, {"documents": docs, "changes": changes}),
    ]

    reflections = applied_reflections(events)

    assert [r.occurred_at for r in reflections] == [later, _AT]
    assert isinstance(reflections[0], AppliedReflection)
    assert reflections[0].documents == (("requirements", 2),)
    assert reflections[0].changes is not None and len(reflections[0].changes) == 1
    assert reflections[1].changes is None
