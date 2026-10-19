"""変更要求(CR)の検証の条件と、反映で付いた印の今の確認の状態のテスト。

SUT: 純粋関数 mark_states・document_items・checked_documents・verification_of・
     with_confirmations と Verification(app/change_requests/verification.py)、
     compare の文書の側の確認の状態(app/change_requests/comparison.py)
ドライバ: 各テスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

from app.change_requests import (
    MARK_CONFIRMATION_LABEL,
    MARK_CONFIRMATIONS,
    AppliedMark,
    DocumentItem,
    MarkState,
    PredictedItem,
    PredictedTarget,
    Prediction,
    checked_documents,
    compare,
    document_items,
    mark_states,
    verification_of,
    with_confirmations,
)
from app.detailed_design.impact import Mark, StageImpact


def _applied(key: str, stage: int) -> AppliedMark:
    return AppliedMark(key=key, label=key, stage=stage, reason="理由", reviewed=False)


def _now(key: str, stage: int, sources: tuple[str, ...], *, reviewed: bool) -> Mark:
    return Mark(key, key, stage, ("req:FR-001", key), "理由", sources, reviewed)


def _item(key: str, stage: int) -> PredictedItem:
    return PredictedItem(
        key=key,
        kind="screen" if stage == 0 else "function",
        stage=stage,
        label=f"{key} の名前",
        path=("req:FR-001", key),
        reason="予測の理由",
        requirement_ids=("FR-001",),
    )


def _prediction(*items: PredictedItem) -> Prediction:
    return Prediction(
        targets=(PredictedTarget("FR-001", "content", "予約を登録する"),),
        items=items,
        unpredicted_new_requirement=False,
    )


def test_mark_states_reads_current_marks() -> None:
    applied = [
        _applied("fn:F-02", 1),  # 今も CR の印があり未確認
        _applied("fn:F-01", 1),  # 確認済み
        _applied("proc:F-01", 5),  # 段階5を承認して印が消えた
        _applied("unit:U-001", 8),  # 承認の後に、別の出どころだけの印が付いた
    ]
    current = {
        1: StageImpact(
            marks=(
                _now("fn:F-01", 1, ("doc:requirements", "cr:1"), reviewed=True),
                _now("fn:F-02", 1, ("doc:requirements", "cr:1"), reviewed=False),
            )
        ),
        8: StageImpact(marks=(_now("unit:U-001", 8, ("stage:7",), reviewed=False),)),
    }

    states = mark_states(applied, current, 1)

    assert [(s.key, s.confirmation) for s in states] == [
        ("fn:F-01", "reviewed"),
        ("fn:F-02", "open"),
        ("proc:F-01", "approved"),
        ("unit:U-001", "approved"),
    ]
    assert [s.confirmed for s in states] == [True, False, True, True]
    # ほかの CR の番号の印は、この CR の印ではない
    assert mark_states([_applied("fn:F-02", 1)], current, 2)[0].confirmation == "approved"


def test_document_items_and_checks() -> None:
    prediction = _prediction(_item("scr:SCR-001", 0), _item("fn:F-01", 1), _item("api:POST", 0))

    items = document_items(prediction, ["api:POST"])

    assert items == [
        DocumentItem("scr:SCR-001", "scr:SCR-001 の名前", "予測の理由", False),
        DocumentItem("api:POST", "api:POST の名前", "予測の理由", True),
    ]
    known = [i.key for i in items]
    # 知らないキーは無視し、並びは予測の順
    assert checked_documents(None, ["api:POST", "x", "scr:SCR-001"], True, known) == [
        "scr:SCR-001",
        "api:POST",
    ]
    assert checked_documents(["api:POST", "scr:SCR-001"], ["api:POST"], False, known) == [
        "scr:SCR-001"
    ]
    # 予測から消えたキーは残さない
    assert checked_documents(["old"], [], True, known) == []


def test_verification_complete_only_when_nothing_open() -> None:
    prediction = _prediction(_item("scr:SCR-001", 0))
    applied = [_applied("fn:F-01", 1)]
    open_mark = {
        1: StageImpact(marks=(_now("fn:F-01", 1, ("doc:requirements", "cr:1"), reviewed=False),))
    }

    pending = verification_of(applied, open_mark, 1, prediction, [])
    assert (pending.open_marks, pending.open_documents, pending.complete) == (1, 1, False)

    done = verification_of(applied, {}, 1, prediction, ["scr:SCR-001"])
    assert (done.open_marks, done.open_documents, done.complete) == (0, 0, True)
    assert verification_of([], {}, 1, _prediction(), []).complete


def test_with_confirmations_and_compare_use_current_state() -> None:
    applied = [_applied("fn:F-01", 1), _applied("cc:認証", 7)]
    states = [
        MarkState("fn:F-01", "fn:F-01", 1, "理由", "approved"),
        MarkState("cc:認証", "cc:認証", 7, "理由", "open"),
    ]
    prediction = _prediction(_item("scr:SCR-001", 0), _item("fn:F-01", 1))

    comparison = compare(
        prediction,
        with_confirmations(applied, states),
        marked_stages={1, 7},
        document_checks={"scr:SCR-001"},
    )

    assert [(i.key, i.reviewed) for i in comparison.items] == [
        ("scr:SCR-001", True),
        ("fn:F-01", True),
        ("cc:認証", False),
    ]
    # 文書の側の確認を渡さなければ、文書の側は None のまま
    assert compare(prediction, [], marked_stages={1}).items[0].reviewed is None


def test_confirmation_labels_cover_every_state() -> None:
    assert set(MARK_CONFIRMATION_LABEL) == set(MARK_CONFIRMATIONS)
