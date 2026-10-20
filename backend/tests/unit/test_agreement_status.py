"""表示中の版の要素ごとの合意の状態の純粋関数のテスト。

SUT: element_states・restored_elements と ELEMENT_STATE_LABEL(app/agreements/status.py。
     パッケージ app.agreements から)
ドライバ: 各テスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

from app.agreements import (
    ELEMENT_STATE_LABEL,
    ELEMENT_STATES,
    USERS_KEY,
    CurrentAgreement,
    agreement_items,
    element_states,
    restored_elements,
)
from app.documents.external_design.model import ExternalDesignModel
from app.documents.requirements.model import RequirementsModel

_REQUIREMENT_KINDS = ("requirement", "users")


def _requirements(*rows: tuple[str, str], users: list[dict] | None = None) -> RequirementsModel:
    return RequirementsModel.model_validate(
        {
            "purpose": "予約を受け付ける",
            "rows": [_row(rid, text) for rid, text in rows],
            "users": users or [],
        }
    )


def _agreement(
    key: str, content: dict, *, kind: str = "requirement", origin_label: str = "B-1"
) -> CurrentAgreement:
    return CurrentAgreement(
        key=key,
        kind=kind,  # type: ignore[arg-type]
        label=f"{key} の合意",
        content=content,
        origin_label=origin_label,
        baseline_number=1 if origin_label == "B-1" else None,
        change_request_number=2 if origin_label == "CR-2" else None,
    )


def _row(rid: str, text: str) -> dict:
    return {"id": rid, "kind": rid.split("-")[0], "text": text, "source": "user"}


def test_states_of_present_elements_and_missing_at_the_end() -> None:
    items = agreement_items(
        _requirements(
            ("FR-001", "予約を登録できる"), ("FR-002", "一覧を見る"), ("FR-003", "新しい")
        ),
        None,
    )
    agreements = [
        _agreement("req:FR-004", _row("FR-004", "消えた行")),
        _agreement("req:FR-002", _row("FR-002", "一覧を見られる"), origin_label="CR-2"),
        _agreement("req:FR-001", _row("FR-001", "予約を登録できる")),
    ]

    states = element_states(items, agreements, _REQUIREMENT_KINDS)

    assert [(s.key, s.state, s.origin_label) for s in states] == [
        ("req:FR-001", "agreed", "B-1"),
        ("req:FR-002", "differs", "CR-2"),
        ("req:FR-003", "unagreed", None),
        ("req:FR-004", "missing", "B-1"),
    ]
    assert states[1].change_request_number == 2 and states[0].baseline_number == 1
    # 表示名は、ある要素は表示中の版、無い要素は合意の記録
    assert states[0].label == "FR-001 予約を登録できる"
    assert states[3].label == "req:FR-004 の合意"
    assert [s.state_label for s in states] == ["合意済み", "合意と違う", "未合意", "今の版に無い"]


def test_saved_defaults_do_not_make_a_difference() -> None:
    """合意の記録の欠けた欄(既定値)は、`body_of`で読み直してそろえるので「違う」にならない。"""
    items = agreement_items(_requirements(("FR-001", "予約を登録できる")), None)
    agreement = _agreement("req:FR-001", _row("FR-001", "予約を登録できる"))

    assert [s.state for s in element_states(items, [agreement], _REQUIREMENT_KINDS)] == ["agreed"]


def test_missing_is_limited_to_the_kinds_of_the_document() -> None:
    """外部設計書の合意は、要件定義書の状態の「今の版に無い」に出さない。"""
    agreements = [_agreement("screen:SCR-001", {"id": "SCR-001", "name": "予約"}, kind="screen")]

    assert element_states([], agreements, _REQUIREMENT_KINDS) == []
    states = element_states([], agreements, ("screen", "api", "integration"))
    assert [(s.key, s.state) for s in states] == [("screen:SCR-001", "missing")]


def test_users_and_external_design_keys() -> None:
    users = [{"user": "店長", "scene": "予約を確かめる", "source": "user"}]
    items = agreement_items(
        _requirements(("FR-001", "予約"), users=users),
        ExternalDesignModel.model_validate(
            {
                "screens": [{"id": "SCR-001", "name": "予約画面"}],
                "apis": [{"method": "GET", "path": "/api/v1/reservations/{id}"}],
                "integrations": [{"service": "Stripe"}],
            }
        ),
    )
    agreements = [
        _agreement(USERS_KEY, {"users": users}, kind="users"),
        # パスの引数名を問わないキー。中身(概要)が違う
        _agreement(
            "api:GET /api/v1/reservations/{}",
            {"method": "GET", "path": "/api/v1/reservations/{rid}", "summary": "1件を返す"},
            kind="api",
        ),
    ]

    states = element_states(items, agreements, ())

    assert [(s.key, s.state) for s in states] == [
        ("req:FR-001", "unagreed"),
        (USERS_KEY, "agreed"),
        ("screen:SCR-001", "unagreed"),
        ("api:GET /api/v1/reservations/{}", "differs"),
        ("integration:Stripe", "unagreed"),
    ]


def test_restored_elements_get_labels() -> None:
    items = agreement_items(_requirements(("FR-001", "予約を登録できる")), None)
    agreements = [_agreement("req:FR-002", _row("FR-002", "一覧"))]

    restored = restored_elements(
        [
            {"key": "req:FR-001", "reason": "changed"},
            {"key": "req:FR-002", "reason": "dropped"},
            {"key": "req:FR-009", "reason": "dropped"},
            {"key": "req:FR-001", "reason": "unknown"},
            {"reason": "changed"},
        ],
        items,
        agreements,
    )

    assert [(r.key, r.label, r.reason) for r in restored] == [
        ("req:FR-001", "FR-001 予約を登録できる", "changed"),
        ("req:FR-002", "req:FR-002 の合意", "dropped"),
        ("req:FR-009", "req:FR-009", "dropped"),
    ]


def test_every_state_has_a_label() -> None:
    assert set(ELEMENT_STATE_LABEL) == set(ELEMENT_STATES)
