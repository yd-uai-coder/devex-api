"""新しい要件を足す変更要求(CR)の予測の起点の保存・確かめ・API のテスト。

SUT: ChangeRequestService.create・update・copy_rejected・impact・explanation・reflection の起点の
     扱いと、純粋関数 checked_origins・origin_keys_of・predict_for・facts_of
     (app/services/change_request_service.py)、BaselineService.origins・baseline_snapshots
     (app/services/baseline_service.py)、リポジトリ create の`origins`とモデルの列、スキーマ
     ChangeRequestCreate・ChangeRequestUpdate の`origins`・ChangeRequestRead.origins・
     ChangeRequestImpactRead.origins・BaselineOriginRead、たどる記録の EDITED_FIELD_LABEL、
     ルート list_baseline_origins(app/api/routes/baselines.py)、要素の履歴の関わる CR
     (AgreementService.history)
ドライバ: 各テスト関数
スタブ: DB はインメモリ SQLite(`db_session`)を本物のリポジトリで使う。設計は要件 FR-001・FR-002・
NFR-001 と段階1〜8 を承認済みにした fixture(`create_impact_project`)から合意済みの版 B-1 を作る
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project

from app.api.routes.baselines import list_baseline_origins
from app.change_requests import EDITED_FIELD_LABEL, ORIGIN_REASON
from app.change_requests.instruction import ORIGIN_LINE
from app.models.project import Project
from app.schemas.baseline import BaselineCreate, BaselineOriginRead
from app.schemas.change_request import (
    ORIGINS_MAX,
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestImpactRead,
    ChangeRequestOriginRead,
    ChangeRequestRead,
    ChangeRequestUpdate,
)
from app.services.agreement_service import AgreementService
from app.services.baseline_service import BaselineService, baseline_snapshots
from app.services.change_request_service import (
    ChangeRequestService,
    ChangeRequestView,
    checked_origins,
    facts_of,
    origin_keys_of,
    predict_for,
)
from app.services.errors import BaselineNotFoundError, ChangeRequestTargetUnknownError

_AT = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
_SCREEN = "screen:SCR-001"
_SCREEN_LABEL = "SCR-001 予約画面"


async def _project(session: AsyncSession) -> Project:
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    return project


def _create(**overrides: object) -> ChangeRequestCreate:
    values: dict[str, object] = {
        "title": "予約にキャンセル待ちを足す",
        "content": "満席の枠にキャンセル待ちで申し込めるようにする",
        "background_source": "meeting",
        "adds_requirement": True,
        "origins": [_SCREEN, " req:FR-001 ", _SCREEN],
    }
    values.update(overrides)
    return ChangeRequestCreate.model_validate(values)


def _read(view: ChangeRequestView) -> ChangeRequestRead:
    return ChangeRequestRead.build(
        view.change_request,
        overlapping=view.overlapping,
        derived_from_number=view.derived_from_number,
        derived_numbers=view.derived_numbers,
        allowed_actions=view.allowed_actions,
    )


async def test_baseline_lists_origin_candidates(db_session: AsyncSession) -> None:
    project = await _project(db_session)

    origins = await list_baseline_origins(1, db_session, project)

    assert all(isinstance(o, BaselineOriginRead) for o in origins)
    assert {o.kind for o in origins} == {"requirement", "screen", "api", "function"}
    assert BaselineOriginRead(key=_SCREEN, kind="screen", label=_SCREEN_LABEL) in origins
    with pytest.raises(BaselineNotFoundError):
        await list_baseline_origins(9, db_session, project)


async def test_create_saves_origins_and_predicts_from_them(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    service = ChangeRequestService(db_session)

    view = await service.create(project, _create(), created_by=None)

    cr = view.change_request
    assert cr.origins == [
        {"key": _SCREEN, "label": _SCREEN_LABEL},
        {"key": "req:FR-001", "label": "FR-001 予約を登録できる"},
    ]
    assert origin_keys_of(cr) == [_SCREEN, "req:FR-001"]
    assert _read(view).origins == [
        ChangeRequestOriginRead(key=_SCREEN, label=_SCREEN_LABEL),
        ChangeRequestOriginRead(key="req:FR-001", label="FR-001 予約を登録できる"),
    ]
    impact = await service.impact(project, 1)
    read = ChangeRequestImpactRead.build(cr, impact.prediction, saved=False, excluded=[])
    assert read.unpredicted_new_requirement is False
    assert [o.key for o in read.origins] == [_SCREEN, "req:FR-001"]
    screen = next(i for i in read.items if i.key == _SCREEN)
    assert (screen.reason, screen.origin_keys) == (ORIGIN_REASON, [_SCREEN, "req:FR-001"])
    explanation = await service.explanation(project, 1)
    # 説明書では名前を先に書く
    assert "新しい要件の予測の起点: 予約画面(SCR-001)・予約を登録できる(FR-001)。" in (
        explanation.explanation.markdown
    )


async def test_origins_are_checked_against_the_baseline(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    service = ChangeRequestService(db_session)

    with pytest.raises(ChangeRequestTargetUnknownError, match="新しい要件を足す CR だけ"):
        await service.create(
            project, _create(adds_requirement=False, targets=[{"requirement_id": "FR-002"}]),
            created_by=None,
        )
    with pytest.raises(ChangeRequestTargetUnknownError, match="screen:SCR-404"):
        await service.create(project, _create(origins=["screen:SCR-404"]), created_by=None)
    with pytest.raises(ChangeRequestTargetUnknownError):  # 起点に選べない種類(処理概要)
        await service.create(project, _create(origins=["summary:F-01"]), created_by=None)
    with pytest.raises(ValidationError):
        _create(origins=[f"req:FR-{n:03}" for n in range(ORIGINS_MAX + 1)])
    baseline = await BaselineService(db_session).get(project, 1)
    assert checked_origins([" ", ""], True, baseline) is None
    assert set(baseline_snapshots(baseline)) >= {"requirements", "external_design"}


async def test_function_origin_is_saved_and_named_in_the_explanation(
    db_session: AsyncSession,
) -> None:
    """段階1 の処理も起点に選べる。説明書では画面と同じく名前を先に書く。"""
    project = await _project(db_session)
    service = ChangeRequestService(db_session)
    label = next(
        o.label for o in await BaselineService(db_session).origins(project, 1) if o.key == "fn:F-01"
    )

    view = await service.create(project, _create(origins=["fn:F-01"]), created_by=None)

    assert view.change_request.origins == [{"key": "fn:F-01", "label": label}]
    impact = await service.impact(project, 1)
    assert "fn:F-01" in {i.key for i in impact.prediction.items}
    explanation = await service.explanation(project, 1)
    name, function_id = label.split(" ", 1)[1], label.split(" ", 1)[0]
    assert f"新しい要件の予測の起点: {name}({function_id})。" in explanation.explanation.markdown


async def test_update_rechecks_and_clears_origins(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(origins=[]), created_by=None)

    added = await service.update(
        project, 1, ChangeRequestUpdate(origins=[_SCREEN]), actor_id=None
    )
    assert origin_keys_of(added.change_request) == [_SCREEN]
    assert added.change_request.events[-1].refs == {"fields": ["origins"]}
    assert EDITED_FIELD_LABEL["origins"] == "予測の起点"
    # null は変えない。新しい要件を足す印を外すときは、起点も外す
    kept = await service.update(project, 1, ChangeRequestUpdate(title="題を直す"), actor_id=None)
    assert origin_keys_of(kept.change_request) == [_SCREEN]
    with pytest.raises(ChangeRequestTargetUnknownError):
        await service.update(
            project,
            1,
            ChangeRequestUpdate.model_validate(
                {"adds_requirement": False, "targets": [{"requirement_id": "FR-002"}]}
            ),
            actor_id=None,
        )
    cleared = await service.update(
        project,
        1,
        ChangeRequestUpdate.model_validate(
            {"adds_requirement": False, "origins": [], "targets": [{"requirement_id": "FR-002"}]}
        ),
        actor_id=None,
    )
    assert cleared.change_request.origins is None
    refs = cleared.change_request.events[-1].refs
    assert refs is not None
    assert set(refs["fields"]) == {
        "targets",
        "adds_requirement",
        "origins",
    }


async def test_saved_prediction_copy_and_instruction_keep_origins(
    db_session: AsyncSession,
) -> None:
    project = await _project(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(origins=[_SCREEN]), created_by=None)
    await service.explain(project, 1, actor_id=None)
    decision = ChangeRequestDecision.model_validate(
        {"decision": "rejected", "decided_at": _AT, "reason": "別の時期に"}
    )
    await service.decide(project, 1, decision, actor_id=None)

    rejected = (await service.get(project, 1)).change_request
    assert rejected.predicted_impact is not None
    assert rejected.predicted_impact["origins"][0]["key"] == _SCREEN
    copied = await service.copy_rejected(project, 1, created_by=None)
    assert copied.change_request.origins == rejected.origins
    facts = facts_of(copied.change_request, 1)
    assert facts.origin_labels == (_SCREEN_LABEL,)
    baseline = await BaselineService(db_session).get(project, 1)
    assert predict_for(copied.change_request, baseline).origins[0].key == _SCREEN
    # 要素の履歴は、保存した予測の起点の画面を、関わる CR として拾う
    history = await AgreementService(db_session).history(project, _SCREEN)
    assert [c.number for c in history.change_requests] == [1]


async def test_reflection_instruction_names_the_origins(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(origins=[_SCREEN]), created_by=None)
    await service.explain(project, 1, actor_id=None)
    decision = ChangeRequestDecision.model_validate(
        {"decision": "agreed", "decided_at": _AT, "reason": "合意"}
    )
    await service.decide(project, 1, decision, actor_id=None)

    reflection = await service.reflection(project.id, 1)

    assert reflection is not None
    assert ORIGIN_LINE.format(origins=_SCREEN_LABEL) in reflection.instruction
