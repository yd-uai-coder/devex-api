"""合意の対象の要素と比べる値・出どころの名前のテスト。

SUT: app.agreements の agreement_items / body_of / integration_key / removed_keys /
     origin_label と、種類・出どころの表(AGREED_KINDS・AGREEMENT_ORIGINS)
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

from tests.fixtures.change_impact import impact_documents

from app.agreements import (
    ADOPT_LABEL,
    AGREED_KINDS,
    AGREEMENT_ORIGINS,
    USERS_KEY,
    USERS_LABEL,
    agreement_items,
    body_of,
    integration_key,
    origin_label,
    removed_keys,
)
from app.detailed_design.impact import DesignModels, build_elements
from app.documents.external_design.model import ExternalDesignModel
from app.documents.requirements.model import RequirementsModel


def _models() -> tuple[RequirementsModel, ExternalDesignModel]:
    documents = impact_documents()
    requirements = RequirementsModel.model_validate(
        {
            **documents["requirements"],
            "users": [
                {"user": "利用者", "scene": "スマホで予約する", "source": "user"},
                {"user": "店員", "scene": "予約を確かめる", "source": "assumed"},
            ],
        }
    )
    external_design = ExternalDesignModel.model_validate(
        {
            **documents["external_design"],
            "integrations": [
                {"service": " メール送信 ", "purpose": "確認メール", "req_ids": ["FR-001"]},
                {"service": "メール送信", "purpose": "重複(捨てる)"},
                {"service": " ", "purpose": "名前が空(捨てる)"},
            ],
        }
    )
    return requirements, external_design


def test_tables() -> None:
    assert AGREED_KINDS == ("requirement", "users", "screen", "api", "integration")
    assert AGREEMENT_ORIGINS == ("baseline", "adopt", "change_request")


def test_items_in_document_order() -> None:
    items = agreement_items(*_models())
    assert [i.key for i in items] == [
        "req:FR-001",
        "req:FR-002",
        "req:NFR-001",
        USERS_KEY,
        "screen:SCR-001",
        "api:POST /api/v1/reservations",
        "api:GET /api/v1/reservations",
        "integration:メール送信",
    ]
    by_key = {i.key: i for i in items}
    assert by_key["req:FR-001"].label == "FR-001 予約を登録できる"
    assert by_key["req:FR-001"].content["id"] == "FR-001"
    assert by_key[USERS_KEY].label == USERS_LABEL
    assert len(by_key[USERS_KEY].content["users"]) == 2
    assert by_key["integration:メール送信"].content["purpose"] == "確認メール"


def test_document_keys_match_change_impact_elements() -> None:
    """要件・画面・API のキーは、変更影響分析の要素のキーと同じ(予測・印・比較と照合するため)。"""
    requirements, external_design = _models()
    items = agreement_items(requirements, external_design)
    models = DesignModels.from_dicts({}, impact_documents(), {})
    elements = {e.key: e for e in build_elements(models)}
    for item in items:
        if item.kind in ("requirement", "screen", "api"):
            assert item.key in elements
            # 比べる値も、要素の body と同じ形
            assert body_of(item.kind, item.content) == elements[item.key].body


def test_missing_documents_give_no_items() -> None:
    assert agreement_items(None, None) == []
    requirements = RequirementsModel(purpose="p")
    assert agreement_items(requirements, None) == []


def test_body_of_fills_defaults_and_drops_key_fields() -> None:
    """保存した形の欠けた欄は既定値でそろえ、キーの欄は比べない。"""
    short = {"id": "FR-001", "kind": "FR", "text": "a", "source": "user"}
    full = {**short, "priority": None, "category": "", "points": []}
    assert body_of("requirement", short) == body_of("requirement", full)
    assert body_of("requirement", short) == body_of("requirement", {**short, "id": "FR-009"})
    assert body_of("requirement", short) != body_of("requirement", {**short, "text": "b"})
    api = {"method": "get", "path": "/x", "summary": "s"}
    assert body_of("api", api) == body_of("api", {**api, "path": "/y"})
    users = {"users": [{"user": "u", "scene": "s", "source": "user"}]}
    changed = {"users": [{"user": "u", "scene": "t", "source": "user"}]}
    assert body_of("users", users) != body_of("users", changed)


def test_integration_key_and_removed_keys() -> None:
    assert integration_key(" 決済 ") == "integration:決済"
    items = agreement_items(*_models())
    assert removed_keys(["req:FR-009", "req:FR-001", USERS_KEY, "screen:SCR-002"], items) == [
        "req:FR-009",
        "screen:SCR-002",
    ]


def test_origin_label() -> None:
    assert origin_label("baseline", baseline_number=2) == "B-2"
    assert origin_label("change_request", cr_number=3) == "CR-3"
    assert origin_label("adopt") == ADOPT_LABEL
    assert origin_label("baseline") == "合意済みの版"
