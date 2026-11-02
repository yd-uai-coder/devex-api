"""古い束を基準にした CR の知らせ(CR の詳細・説明書・共有リンクの作成)と、規模の「古い」の状態の
テスト。

SUT: 純粋 ── Freshness.excluding、ScaleCount.state・with_baseline_changes・STALE_NOTE_KINDS、
     build_explanation の規模の節(基準の束の行と、束の後の CR の注記)、UNDECIDED_STATUSES
     サービス ── ChangeRequestService(詳細・説明書の基準の束の鮮度)・BaselineService.freshness_in・
     ShareLinkService.create、ルート get_change_request・get_change_request_explanation・
     create_change_request_share_link(ChangeRequestRead・ChangeRequestExplanationRead・
     ScaleCountRead・ShareLinkCreatedRead の鮮度と状態)
ドライバ: pytest のテスト関数(ルートは関数を直接呼ぶ)
スタブ: 純粋の部分はスタブ不要 ── 副作用が無く外部依存を呼ばないため。サービスの部分は、束と
     反映済みの CR を偽の LLM で反映する fixture(tests/fixtures/change_requests.py)で作り、DB は
     テスト用の SQLite
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import impact_documents, impact_stages
from tests.fixtures.change_requests import (
    agreed_change_request_project,
    applied_change_request_project,
)

from app.api.routes.change_requests import (
    create_change_request_share_link,
    get_change_request,
    get_change_request_explanation,
)
from app.change_requests import (
    STALE_NOTE_KINDS,
    UNDECIDED_STATUSES,
    AppliedAfter,
    ChangeRequestFacts,
    Freshness,
    Target,
    build_explanation,
    predict_from_snapshots,
    predicted_scale,
    with_baseline_changes,
)
from app.models.user import User
from app.schemas.change_request import ChangeRequestCreate
from app.schemas.share_link import ShareLinkCreate
from app.services.baseline_service import BaselineService
from app.services.change_request_service import ChangeRequestService
from app.services.share_link_service import ShareLinkService


def _snapshots() -> dict[str, dict]:
    stages = {f"stage:{n}": model for n, model in impact_stages().items()}
    return {**impact_documents(), **stages}


def _explanation(changed_after: list[int], status: str = "draft"):
    snapshots = _snapshots()
    targets = (Target("FR-001"),)
    facts = ChangeRequestFacts(
        number=2,
        title="登録を変える",
        status=status,  # type: ignore[arg-type]
        content="登録の後にメールを送る",
        background_source="meeting",
        background_date=date(2026, 10, 28),
        background_memo="",
        targets=targets,
        adds_requirement=False,
        baseline_number=1,
    )
    prediction = predict_from_snapshots(snapshots, list(targets), adds_requirement=False)
    return build_explanation(facts, prediction, snapshots, [], {}, changed_after)


def test_freshness_excluding_drops_own_change_request() -> None:
    freshness = Freshness(1, (), (AppliedAfter(1, ("req:FR-001",)), AppliedAfter(2, ())), ())

    assert [a.number for a in freshness.excluding(1).applied_after] == [2]
    assert not freshness.excluding(1).fresh
    assert freshness.excluding(1).excluding(2).fresh


def test_scale_state_separates_stale_from_uncountable() -> None:
    snapshots = _snapshots()
    targets = [Target("FR-001")]
    prediction = predict_from_snapshots(snapshots, targets, adds_requirement=False)
    scale = predicted_scale(targets, prediction, snapshots, {})
    new_requirement = predicted_scale(
        [],
        predict_from_snapshots(snapshots, [], adds_requirement=True),
        snapshots,
        {},
    )

    # 束にテスト仕様書が無いので、テストだけ数えられない
    assert [(c.kind, c.state) for c in scale.counts if c.state != "counted"] == [
        ("test", "uncountable")
    ]
    assert with_baseline_changes(scale, 1, []) is scale
    changed = with_baseline_changes(scale, 1, [2, 3])
    assert {c.state for c in changed.counts} == {"stale", "uncountable"}
    assert changed.of("test").notes == ()
    note = changed.of("unit").notes[-1]
    assert (note.code, note.detail) == ("baseline_changed", "B-1 の後に CR-2・CR-3")
    assert {"stage_outdated", "baseline_changed"} == STALE_NOTE_KINDS
    # 古い段階の注記も「古い」。数えられないものは注記を足さない
    outdated = predicted_scale(targets, prediction, snapshots, {7: "outdated"})
    assert outdated.of("unit").state == "stale"
    stale_new = with_baseline_changes(new_requirement, 1, [2])
    assert stale_new.of("unit").state == "uncountable"
    assert stale_new.of("unit").notes == ()


def test_scale_section_names_baseline_and_notes_later_change_requests() -> None:
    plain = _explanation([]).markdown
    stale = _explanation([3]).markdown

    assert "合意済みの版 B-1 の設計から数えました。" in plain
    assert "今の設計と違うことがある" not in plain
    assert (
        "合意済みの版の後に別の変更を反映したため、今の設計と違うことがある(B-1 の後に CR-3)"
        in stale
    )
    assert {"draft", "explained"} == UNDECIDED_STATUSES


async def test_change_request_on_stale_baseline_is_reported(db_session: AsyncSession) -> None:
    project = await applied_change_request_project(db_session)
    user = await db_session.get(User, project.user_id)
    assert user is not None
    service = ChangeRequestService(db_session)
    created = await service.create(
        project,
        ChangeRequestCreate.model_validate(
            {
                "title": "一覧を日付順にする",
                "content": "一覧を日付の新しい順にする",
                "background_source": "email",
                "targets": [{"requirement_id": "FR-002"}],
            }
        ),
        created_by=None,
    )

    # B-1 の後に CR-1 を反映したので、CR-2 の基準の束は古い(止めずに知らせる)
    assert created.baseline_freshness is not None
    assert [a.number for a in created.baseline_freshness.applied_after] == [1]
    read = await get_change_request(2, db_session, project)
    assert read.baseline_freshness is not None
    assert (read.baseline_freshness.label, read.baseline_freshness.fresh) == ("B-1", False)
    # CR-1 自身の反映は、CR-1 の基準の束を古くしたとは言わない
    own = await get_change_request(1, db_session, project)
    assert own.baseline_freshness is not None and own.baseline_freshness.fresh

    explanation = await get_change_request_explanation(2, db_session, project)
    assert explanation.baseline_freshness is not None
    assert not explanation.baseline_freshness.fresh
    assert "(B-1 の後に CR-1)" in explanation.markdown
    assert {c.state for c in explanation.scale if c.count is not None} == {"stale"}
    # 反映済みの CR-1 の説明書には、束の後の CR の注記を足さない
    applied = await get_change_request_explanation(1, db_session, project)
    assert "今の設計と違うことがある" not in applied.markdown

    shared = await create_change_request_share_link(
        2, ShareLinkCreate(), db_session, project, user
    )
    assert shared.baseline_freshness is not None
    assert [a.number for a in shared.baseline_freshness.applied_after] == [1]
    baseline = await BaselineService(db_session).get(project, 1)
    by_id = await BaselineService(db_session).freshness_in(project.id, [baseline])
    assert not by_id[baseline.id].fresh


async def test_fresh_baseline_is_reported_as_fresh(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)
    await ChangeRequestService(db_session).create(
        project,
        ChangeRequestCreate.model_validate(
            {"title": "一覧", "content": "一覧を変える", "background_source": "other",
             "targets": [{"requirement_id": "FR-002"}]}
        ),
        created_by=None,
    )

    created = await ShareLinkService(db_session).create(
        project, 2, ShareLinkCreate(), actor_id=None
    )

    assert created.baseline_freshness is not None and created.baseline_freshness.fresh
    explanation = await ChangeRequestService(db_session).explanation(project, 2)
    assert explanation.baseline_freshness is not None and explanation.baseline_freshness.fresh
