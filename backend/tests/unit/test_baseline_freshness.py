"""合意済みの版(束)の鮮度のテスト。

SUT: 純粋関数 app/change_requests/freshness.py の freshness(と scale.outdated_stages)、
     BaselineService(freshness / freshness_of)・BaselineRepository.numbers・
     AgreementService.record_facts・baseline_stage_states、ルート get_baseline_freshness と、
     list_baselines / get_baseline の`fresh`、BaselineFreshnessRead
ドライバ: pytest のテスト関数(ルートは関数を直接呼ぶ)
スタブ: 純粋関数のテストはスタブ不要 ── 合意の記録を RecordFact で直接渡す。サービスのテストは、
     CR の反映に偽の LLM を使う fixture(tests/fixtures/change_requests.py)と、テスト用の SQLite
"""

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_requests import (
    AGREED_AT,
    agreed_change_request_project,
    applied_change_request_project,
)

from app.api.routes.baselines import get_baseline, get_baseline_freshness, list_baselines
from app.change_requests import Freshness, RecordFact, freshness, outdated_stages
from app.change_requests.freshness import AppliedAfter
from app.repositories.baseline import BaselineRepository
from app.schemas.baseline import BaselineCreate
from app.services.agreement_service import AgreementService
from app.services.baseline_service import BaselineService, baseline_stage_states

_B1, _B2 = "b1", "b2"


def _rec(
    seq: int,
    key: str,
    text: str | None,
    *,
    baseline: str | None = None,
    cr: int | None = None,
) -> RecordFact:
    return RecordFact(
        seq=seq,
        key=key,
        origin="baseline" if baseline else "change_request",
        baseline_id=baseline,
        change_request_number=cr,
        content=None if text is None else {"text": text},
    )


_BASE = [
    _rec(1, "req:FR-001", "登録する", baseline=_B1),
    _rec(2, "screen:SCR-001", "一覧", baseline=_B1),
]


def _fresh(records: list[RecordFact], **kwargs: object) -> Freshness:
    values: dict = {
        "baseline_id": _B1,
        "baseline_number": 1,
        "baseline_numbers": [1],
        "stage_states": {1: "approved", 7: "approved"},
    }
    values.update(kwargs)
    return freshness(records, **values)


def test_latest_without_changes_is_fresh() -> None:
    result = _fresh(_BASE)

    assert (result.fresh, result.is_latest, result.tracked) == (True, True, True)
    assert (result.applied_after, result.outdated_stages, result.newer_numbers) == ((), (), ())


def test_change_requests_applied_after_baseline_in_order() -> None:
    records = [
        *_BASE,
        _rec(4, "screen:SCR-001", "一覧と検索", cr=3),
        _rec(3, "req:FR-001", "登録して知らせる", cr=2),
        _rec(5, "req:FR-001", "登録して必ず知らせる", cr=3),
    ]

    result = _fresh(records)

    assert not result.fresh
    assert result.applied_after == (
        AppliedAfter(2, ("req:FR-001",)),
        AppliedAfter(3, ("screen:SCR-001", "req:FR-001")),
    )


def test_reflection_that_kept_text_is_not_counted() -> None:
    """文を変えなかった反映(偽の LLM・やり直し)は、束の後の変更に数えない(競合と同じ規則)。"""
    result = _fresh([*_BASE, _rec(3, "req:FR-001", "登録する", cr=2)])

    assert result.fresh


def test_change_request_applied_before_baseline_is_not_counted() -> None:
    """束に入れた CR は束より前に反映しているので、その束の後の変更ではない。"""
    records = [
        _rec(1, "req:FR-001", "登録する", baseline=_B1),
        _rec(2, "req:FR-001", "登録して知らせる", cr=1),
        _rec(3, "req:FR-001", "登録して知らせる", baseline=_B2),
    ]

    old = _fresh(records, baseline_numbers=[1, 2])
    new = _fresh(records, baseline_id=_B2, baseline_number=2, baseline_numbers=[1, 2])

    assert (old.is_latest, old.newer_numbers, old.applied_after) == (
        False,
        (2,),
        (AppliedAfter(1, ("req:FR-001",)),),
    )
    assert new.fresh


def test_outdated_stages_at_creation() -> None:
    result = _fresh(_BASE, stage_states={8: "outdated", 1: "approved", 3: "reviewing"})

    assert (result.fresh, result.outdated_stages) == (False, (3, 8))
    assert outdated_stages({1: "approved", 2: "regenerated"}) == frozenset({2})


def test_baseline_without_records_is_untracked() -> None:
    result = _fresh([_rec(5, "req:FR-001", "変えた", cr=1)])

    assert (result.tracked, result.applied_after, result.fresh) == (False, (), True)


async def test_service_and_routes_report_change_request_applied_after_baseline(
    db_session: AsyncSession,
) -> None:
    project = await applied_change_request_project(db_session)
    service = BaselineService(db_session)

    read = await get_baseline_freshness(1, db_session, project)

    assert (read.label, read.fresh, read.is_latest, read.tracked) == ("B-1", False, True, True)
    # 反映は対象の要件と、予測が届いた画面・API を書き換えた(記録の順)
    [applied] = read.applied_after
    assert (applied.number, applied.keys[0]) == (1, "req:FR-001")
    assert {k.split(":")[0] for k in applied.keys} == {"req", "screen", "api"}
    assert read.outdated_stages == []
    assert (await get_baseline(1, db_session, project)).fresh is False

    await service.create(
        project, BaselineCreate(agreed_at=AGREED_AT, reason="反映の後"), recorded_by=None
    )

    listed = await list_baselines(db_session, project)
    assert [(b.number, b.fresh) for b in listed] == [(2, True), (1, False)]
    old = await service.freshness(project, 1)
    assert (old.is_latest, old.newer_numbers) == (False, (2,))
    assert await BaselineRepository(db_session).numbers(project.id) == [1, 2]


async def test_baseline_before_reflection_is_fresh(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)

    baseline = await BaselineService(db_session).get(project, 1)
    result = await BaselineService(db_session).freshness(project, 1)

    assert result.fresh
    assert set(baseline_stage_states(baseline).values()) == {"approved"}
    facts = await AgreementService(db_session).record_facts(project.id)
    assert facts and all(f.origin == "baseline" for f in facts)
