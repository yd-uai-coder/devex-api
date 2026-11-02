"""合意済みの要素を合意の形へ戻す純粋関数のテスト。

SUT: app.agreements の protect_requirements / agreed_requirement_ids / requirement_key と
     戻した理由の表(RESTORE_REASONS)、利用者の比べる値(body_of)と戻したときの ID
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

from app.agreements import (
    RESTORE_REASONS,
    USERS_KEY,
    Restored,
    agreed_requirement_ids,
    agreement_items,
    protect_requirements,
    requirement_key,
)
from app.documents.requirements.model import RequirementRow, RequirementsModel, UserScene


def _row(req_id: str, text: str, source: str = "adopted", kind: str = "FR") -> RequirementRow:
    return RequirementRow.model_validate(
        {
            "id": req_id,
            "kind": kind,
            "priority": "Must" if kind == "FR" else None,
            "category": "" if kind == "FR" else "性能・拡張性",
            "text": text,
            "source": source,
            "points": ["core"],
        }
    )


def _model(*rows: RequirementRow, users: list[UserScene] | None = None) -> RequirementsModel:
    return RequirementsModel(
        purpose="予約を受ける",
        users=users or [],
        rows=list(rows),
        next_seq={"FR": 4, "NFR": 2, "CON": 1},
    )


def _agreed(model: RequirementsModel) -> dict[str, dict]:
    """束の作成と同じ形の、今の合意(キー → 中身)。"""
    return {item.key: item.content for item in agreement_items(model, None)}


_USERS = [UserScene(user="利用者", scene="スマホで予約する", source="adopted")]
_PREVIOUS = _model(
    _row("FR-001", "予約できる"),
    _row("FR-002", "取り消せる"),
    _row("FR-003", "一覧で見られる", source="assumed"),
    _row("NFR-001", "1秒で返す", kind="NFR"),
    users=_USERS,
)


def test_smoke_unchanged_model_passes_through() -> None:
    model, restored = protect_requirements(_PREVIOUS, _PREVIOUS, _agreed(_PREVIOUS))
    assert restored == []
    assert model is _PREVIOUS


def test_tables_and_keys() -> None:
    assert RESTORE_REASONS == ("changed", "dropped")
    assert requirement_key("FR-001") == "req:FR-001"
    agreed = _agreed(_PREVIOUS)
    assert agreed_requirement_ids(agreed, frozenset({"req:FR-002"})) == {
        "FR-001",
        "FR-003",
        "NFR-001",
    }


def test_no_agreement_returns_model_as_is() -> None:
    model, restored = protect_requirements(_PREVIOUS, None, {})
    assert model is _PREVIOUS
    assert restored == []


def test_rewritten_row_is_restored_with_every_field() -> None:
    # 出どころの上限のかけ直し(採用 → 推測)も、文の書き換えも、合意の中身に戻す
    generated = _model(
        _row("FR-001", "予約できる", source="assumed"),
        _row("FR-002", "キャンセルできる"),
        _row("FR-003", "一覧で見られる", source="assumed"),
        _row("NFR-001", "1秒で返す", kind="NFR"),
        users=_USERS,
    )
    model, restored = protect_requirements(generated, _PREVIOUS, _agreed(_PREVIOUS))
    assert restored == [Restored("req:FR-001", "changed"), Restored("req:FR-002", "changed")]
    assert model.rows == _PREVIOUS.rows


def test_assumed_row_in_agreement_stays_assumed() -> None:
    # 束に入った推測の行は、合意の形(推測)のまま。上限を上げも下げもしない
    generated = _model(*_PREVIOUS.rows[:2], _row("FR-003", "一覧で見られる", source="user"))
    model, _ = protect_requirements(generated, _PREVIOUS, _agreed(_PREVIOUS))
    assert next(r for r in model.rows if r.id == "FR-003").source == "assumed"


def test_dropped_row_goes_back_after_previous_neighbour() -> None:
    generated = _model(
        _row("FR-001", "予約できる"),
        _row("FR-003", "一覧で見られる", source="assumed"),
        _row("FR-004", "通知する", source="assumed"),
        _row("NFR-001", "1秒で返す", kind="NFR"),
        users=_USERS,
    )
    model, restored = protect_requirements(generated, _PREVIOUS, _agreed(_PREVIOUS))
    assert restored == [Restored("req:FR-002", "dropped")]
    assert [r.id for r in model.rows] == ["FR-001", "FR-002", "FR-003", "FR-004", "NFR-001"]


def test_dropped_first_rows_go_back_to_the_head_of_their_kind() -> None:
    # 前の版で先頭だった行が続けて落ちても、前の版の並びに戻る
    generated = _model(
        _row("FR-003", "一覧で見られる", source="assumed"),
        _row("NFR-001", "1秒で返す", kind="NFR"),
        users=_USERS,
    )
    model, _ = protect_requirements(generated, _PREVIOUS, _agreed(_PREVIOUS))
    assert [r.id for r in model.rows] == ["FR-001", "FR-002", "FR-003", "NFR-001"]


def test_dropped_row_missing_from_previous_goes_to_end_of_kind() -> None:
    agreed = _agreed(_PREVIOUS)
    previous = _model(*_PREVIOUS.rows[:2], _PREVIOUS.rows[3], users=_USERS)  # FR-003 が無い版
    generated = _model(*_PREVIOUS.rows[:2], _PREVIOUS.rows[3], users=_USERS)
    model, restored = protect_requirements(generated, previous, agreed)
    assert restored == [Restored("req:FR-003", "dropped")]
    assert [r.id for r in model.rows] == ["FR-001", "FR-002", "FR-003", "NFR-001"]


def test_exempt_rows_may_change_or_disappear() -> None:
    # 反映する CR の対象(内容の変更・削除)は戻さない
    generated = _model(
        _row("FR-001", "予約を2件までできる"),
        _row("FR-003", "一覧で見られる", source="assumed"),
        _row("NFR-001", "1秒で返す", kind="NFR"),
        users=_USERS,
    )
    model, restored = protect_requirements(
        generated,
        _PREVIOUS,
        _agreed(_PREVIOUS),
        exempt=frozenset({"req:FR-001", "req:FR-002"}),
    )
    assert restored == []
    assert [r.text for r in model.rows][:2] == ["予約を2件までできる", "一覧で見られる"]


def test_new_row_with_the_same_text_as_agreed_row_is_removed() -> None:
    # 合意済みの行を落として、同じ文を新しい ID で書いた(言い換えではない重複)
    generated = _model(
        _row("FR-001", "予約できる"),
        _row("FR-003", "一覧で見られる", source="assumed"),
        _row("FR-004", " 取り消せる ", source="assumed"),
        _row("FR-005", "取り消しを通知する", source="assumed"),
        _row("NFR-001", "1秒で返す", kind="NFR"),
        users=_USERS,
    )
    model, restored = protect_requirements(generated, _PREVIOUS, _agreed(_PREVIOUS))
    assert restored == [Restored("req:FR-002", "dropped")]
    assert [r.id for r in model.rows] == ["FR-001", "FR-002", "FR-003", "FR-005", "NFR-001"]


def test_users_are_restored_as_a_whole() -> None:
    two = [*_USERS, UserScene(user="店員", scene="予約を確かめる", source="assumed")]
    previous = _model(*_PREVIOUS.rows, users=two)
    agreed = _agreed(previous)
    one = _model(*_PREVIOUS.rows, users=_USERS)
    changed, restored = protect_requirements(one, previous, agreed)
    assert restored == [Restored(USERS_KEY, "changed")]
    assert changed.users == previous.users
    dropped, restored = protect_requirements(_model(*_PREVIOUS.rows), previous, agreed)
    assert restored == [Restored(USERS_KEY, "dropped")]
    assert dropped.users == previous.users


def test_user_ids_are_not_compared_and_legacy_agreement_gets_ids_by_name() -> None:
    """利用者の ID は比べる値に入らない(ID を振る前の合意も「合意と違う」にならない)。ID の無い
    合意の形に戻すときは、前の版の同じ名前の行の ID を引き継ぐ。"""
    previous = _model(*_PREVIOUS.rows, users=_USERS)  # 利用者 = U-001
    legacy_user = {"user": "利用者", "scene": "スマホで予約する", "source": "adopted"}
    legacy = {**_agreed(previous), USERS_KEY: {"users": [legacy_user]}}
    _, restored = protect_requirements(previous, previous, legacy)
    assert restored == []
    renamed = _model(
        *_PREVIOUS.rows,
        users=[UserScene(id="U-002", user="お客様", scene="スマホで予約する", source="adopted")],
    )
    model, restored = protect_requirements(renamed, previous, legacy)
    assert restored == [Restored(USERS_KEY, "changed")]
    assert [(u.id, u.user) for u in model.users] == [("U-001", "利用者")]
    assert model.next_seq["U"] == 3


def test_next_seq_skips_restored_ids() -> None:
    # 戻した行の番号を、次に振る番号が越える(消えた ID を使い回さない)
    previous = _model(_row("FR-007", "予約できる"))
    generated = RequirementsModel(purpose="予約を受ける", rows=[], next_seq={"FR": 2})
    model, _ = protect_requirements(generated, previous, _agreed(previous))
    assert model.next_seq["FR"] == 8
