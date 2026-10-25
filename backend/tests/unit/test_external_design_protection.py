"""外部設計書の合意済みの要素を合意の形へ戻す純粋関数のテスト。

SUT: app.agreements の protect_external_design / agreed_external_ids(AgreedExternalIds) /
     service_match_key(外部連携の重複の照合)
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

from app.agreements import (
    AgreedExternalIds,
    Restored,
    agreed_external_ids,
    agreement_items,
    integration_key,
    protect_external_design,
    service_match_key,
)
from app.ai.llm.paraphrase import paraphrase_service
from app.documents.external_design.model import (
    ApiRow,
    ExternalDesignModel,
    IntegrationRow,
    ScreenRow,
)


def _screen(screen_id: str, name: str, role: str = "") -> ScreenRow:
    return ScreenRow(id=screen_id, name=name, role=role or f"{name}の役割", req_ids=["FR-001"])


def _api(method: str, path: str, summary: str = "") -> ApiRow:
    return ApiRow(method=method, path=path, summary=summary or f"{method} の概要")


def _integration(service: str, purpose: str = "通知") -> IntegrationRow:
    return IntegrationRow(service=service, purpose=purpose)


def _model(
    screens: list[ScreenRow],
    apis: list[ApiRow] | None = None,
    integrations: list[IntegrationRow] | None = None,
    *,
    next_seq: int = 4,
) -> ExternalDesignModel:
    return ExternalDesignModel(
        overview="予約のシステム",
        screens=screens,
        apis=apis or [],
        integrations=integrations or [],
        next_seq={"SCR": next_seq},
    )


def _agreed(model: ExternalDesignModel) -> dict[str, dict]:
    """束の作成と同じ形の、今の合意(キー → 中身)。"""
    return {item.key: item.content for item in agreement_items(None, model)}


_PREVIOUS = _model(
    [
        _screen("SCR-001", "予約画面"),
        _screen("SCR-002", "一覧画面"),
        _screen("SCR-003", "詳細画面"),
    ],
    [_api("GET", "/api/v1/reservations"), _api("POST", "/api/v1/reservations")],
    [_integration("メール配信"), _integration("決済")],
)


def test_smoke_unchanged_model_passes_through() -> None:
    model, restored = protect_external_design(_PREVIOUS, _PREVIOUS, _agreed(_PREVIOUS))
    assert restored == []
    assert model is _PREVIOUS


def test_agreed_external_ids_skip_requirements_and_exempt() -> None:
    agreed = {"req:FR-001": {}, "users": {}, **_agreed(_PREVIOUS)}
    ids = agreed_external_ids(agreed, frozenset({"screen:SCR-002"}))
    assert ids == AgreedExternalIds(
        screen_ids=frozenset({"SCR-001", "SCR-003"}),
        endpoints=frozenset({"GET /api/v1/reservations", "POST /api/v1/reservations"}),
        services=frozenset({"メール配信", "決済"}),
    )
    assert integration_key("メール配信") in agreed


def test_changed_screen_and_api_are_replaced() -> None:
    generated = _PREVIOUS.model_copy(
        update={
            "overview": "書き換えた概要",
            "screens": [
                _screen("SCR-001", "予約画面", role="書き換えた役割"),
                *_PREVIOUS.screens[1:],
            ],
            "apis": [_api("GET", "/api/v1/reservations", "書き換えた"), _PREVIOUS.apis[1]],
        }
    )
    model, restored = protect_external_design(generated, _PREVIOUS, _agreed(_PREVIOUS))
    assert restored == [
        Restored("screen:SCR-001", "changed"),
        Restored("api:GET /api/v1/reservations", "changed"),
    ]
    assert model.screens == _PREVIOUS.screens
    assert model.apis == _PREVIOUS.apis
    # 概要は合意の対象ではない
    assert model.overview == "書き換えた概要"


def test_dropped_rows_return_to_previous_position() -> None:
    generated = _model(
        [_PREVIOUS.screens[0], _PREVIOUS.screens[2], _screen("SCR-004", "新しい画面")],
        [_PREVIOUS.apis[1]],
        [_integration("決済")],
        next_seq=5,
    )
    model, restored = protect_external_design(generated, _PREVIOUS, _agreed(_PREVIOUS))
    assert restored == [
        Restored("screen:SCR-002", "dropped"),
        Restored("api:GET /api/v1/reservations", "dropped"),
        Restored(integration_key("メール配信"), "dropped"),
    ]
    assert [s.id for s in model.screens] == ["SCR-001", "SCR-002", "SCR-003", "SCR-004"]
    assert [a.label for a in model.apis] == [
        "GET /api/v1/reservations",
        "POST /api/v1/reservations",
    ]
    assert [i.service for i in model.integrations] == ["メール配信", "決済"]


def test_renumbered_screen_with_same_name_is_removed() -> None:
    """ID だけを付け直した画面(合意済みの画面と同じ名前)は除き、合意済みの ID に戻す。"""
    generated = _model(
        [_PREVIOUS.screens[0], _screen("SCR-004", "一覧画面"), _PREVIOUS.screens[2]],
        _PREVIOUS.apis,
        _PREVIOUS.integrations,
        next_seq=5,
    )
    model, restored = protect_external_design(generated, _PREVIOUS, _agreed(_PREVIOUS))
    assert restored == [Restored("screen:SCR-002", "dropped")]
    assert [s.id for s in model.screens] == ["SCR-001", "SCR-002", "SCR-003"]


def test_service_match_key_ignores_case_spaces_marks_and_trailing_api() -> None:
    assert service_match_key("Google Maps API") == service_match_key("google maps")
    assert service_match_key("［E2E Fake］ 予約通知API") == service_match_key("[E2E Fake] 予約通知")
    assert service_match_key("Google Maps") != service_match_key("Google Calendar")
    assert service_match_key("API") == "api"


def test_paraphrased_service_of_agreed_integration_is_not_kept_twice() -> None:
    """合意済みの外部連携のサービス名を言い換えた行(「X API」→「X」)は除き、合意の行に戻す。
    正規化して違う名前の新しい行は残す。"""
    previous = _model([], [], [_integration("地図 API"), _integration("決済")])
    generated = _model(
        [],
        [],
        [
            _integration(paraphrase_service("地図 API")),
            _integration("決済"),
            _integration("メール配信"),
        ],
    )
    model, restored = protect_external_design(generated, previous, _agreed(previous))
    assert restored == [Restored(integration_key("地図 API"), "dropped")]
    assert [i.service for i in model.integrations] == ["地図 API", "決済", "メール配信"]


def test_integrations_without_agreement_are_not_merged() -> None:
    """重複を除くのは、合意済みの行と同じ連携だけ(合意の無い行どうしは触らない)。"""
    previous = _model([], [], [_integration("決済")])
    services = ["決済", "地図", "地図 API"]
    generated = _model([], [], [_integration(s) for s in services])
    model, restored = protect_external_design(generated, previous, _agreed(previous))
    assert restored == []
    assert [i.service for i in model.integrations] == services


def test_restored_screen_id_advances_next_seq() -> None:
    previous = _model([_screen("SCR-007", "予約画面")], next_seq=8)
    generated = _model([], next_seq=1)
    model, _ = protect_external_design(generated, previous, _agreed(previous))
    assert model.next_seq["SCR"] == 8


def test_exempt_elements_may_change_or_disappear() -> None:
    """保存した予測の画面・API(CR で書き換えてよい要素)は戻さない。外部連携は守る。"""
    generated = _model(
        [_screen("SCR-001", "予約画面", role="CR で変えた"), *_PREVIOUS.screens[1:]],
        [_PREVIOUS.apis[1]],
        [_integration("メール配信", purpose="書き換えた"), _integration("決済")],
    )
    exempt = frozenset({"screen:SCR-001", "api:GET /api/v1/reservations"})
    model, restored = protect_external_design(
        generated, _PREVIOUS, _agreed(_PREVIOUS), exempt=exempt
    )
    assert restored == [Restored(integration_key("メール配信"), "changed")]
    assert model.screens[0].role == "CR で変えた"
    assert [a.label for a in model.apis] == ["POST /api/v1/reservations"]
    assert model.integrations[0].purpose == "通知"


def test_new_elements_without_agreement_are_kept() -> None:
    generated = _PREVIOUS.model_copy(
        update={
            "apis": [*_PREVIOUS.apis, _api("DELETE", "/api/v1/reservations/{id}")],
            "integrations": [*_PREVIOUS.integrations, _integration("地図")],
        }
    )
    model, restored = protect_external_design(generated, _PREVIOUS, _agreed(_PREVIOUS))
    assert restored == []
    assert len(model.apis) == 3
    assert [i.service for i in model.integrations] == ["メール配信", "決済", "地図"]


def test_api_key_ignores_path_parameter_name() -> None:
    """API は変更影響分析と同じキー(パスの引数名を問わない)で照合する。"""
    previous = _model([], [_api("GET", "/api/v1/reservations/{id}")])
    generated = _model([], [_api("GET", "/api/v1/reservations/{reservation_id}")])
    model, restored = protect_external_design(generated, previous, _agreed(previous))
    # 中身(キーの欄を除く)は同じなので、戻さない
    assert restored == []
    assert model is generated


def test_no_agreement_returns_model_as_is() -> None:
    model, restored = protect_external_design(_PREVIOUS, None, {})
    assert model is _PREVIOUS
    assert restored == []
