"""確認事項の一覧の組み立てと保存、「採用にする」との接続のテスト。

SUT: ConfirmationService(view / update)と、DocGeneratorService の apply_adopt_requirement /
     apply_adopt_user / adopt_requirement(確認済みと合意の記録)、
     AgreementService.record_adopt_users、
     schemas の confirmation_list_read・ConfirmationUpdate、エラー ConfirmationItemNotFoundError、
     ルート app/api/routes/confirmations.py(アプリへの登録の確かめ)
ドライバ: pytest のテスト関数
スタブ不要 ── LLM を呼ばない経路(一覧の組み立て・欄の保存・採用)だけで、DB はテスト用の SQLite の
セッションを使い、本物のリポジトリを通す
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.confirmations import router as confirmations_router
from app.documents.requirements import RequirementRow, RequirementsModel, UserScene
from app.hearing import PointState
from app.main import app
from app.models.project import Project
from app.models.user import User
from app.repositories.agreement import AgreementRepository
from app.repositories.confirmation import ConfirmationRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.hearing_point import HearingPointRepository
from app.schemas.confirmation import ConfirmationUpdate
from app.services.confirmation_service import ConfirmationService
from app.services.doc_generator_service import DocGeneratorService, parse_requirements_model
from app.services.errors import ConfirmationItemNotFoundError, DesignStagesNotAvailableError


def _model() -> RequirementsModel:
    return RequirementsModel(
        purpose="目的",
        users=[
            UserScene(user="担当者", scene="毎朝確認する", source="user"),
            UserScene(user="管理者", scene="月末に集計する", source="assumed"),
        ],
        rows=[
            RequirementRow(
                id="FR-001", kind="FR", priority="Must", text="記事を集める",
                source="user", points=["core"],
            ),
            RequirementRow(
                id="FR-002", kind="FR", priority="Should", text="通知する",
                source="assumed", points=["exceptions"],
            ),
        ],
    )


async def _project(session: AsyncSession, *, mode: str = "detailed") -> Project:
    user = User(email=f"c-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode=mode, status="completed")
    session.add(project)
    await session.flush()
    await GeneratedDocumentRepository(session).create_version(
        project_id=project.id,
        doc_type="requirements",
        content="# r",
        model=_model().model_dump(mode="json"),
    )
    await HearingPointRepository(session).save_states(
        project.id, [PointState("core", "decided", "集める")]
    )
    await session.commit()
    return project


async def _current_model(session: AsyncSession, project: Project) -> RequirementsModel:
    document = await GeneratedDocumentRepository(session).get_current(
        project_id=project.id, doc_type="requirements"
    )
    assert document is not None
    model = parse_requirements_model(document.model)
    assert model is not None
    return model


def test_router_is_registered() -> None:
    paths = {route.path for route in confirmations_router.routes}  # type: ignore[attr-defined]
    assert paths == {
        "/projects/{project_id}/confirmations",
        "/projects/{project_id}/confirmations/{key:path}",
    }
    paths = app.openapi()["paths"]
    assert "/api/v1/projects/{project_id}/confirmations" in paths
    assert "patch" in paths["/api/v1/projects/{project_id}/confirmations/{key}"]


def test_update_rejects_null_status_and_long_memo() -> None:
    assert ConfirmationUpdate.model_validate({"opportunity": None}).model_dump(
        exclude_unset=True
    ) == {"opportunity": None}
    with pytest.raises(ValidationError):
        ConfirmationUpdate.model_validate({"status": None})
    with pytest.raises(ValidationError):
        ConfirmationUpdate.model_validate({"memo": "x" * 2001})


async def test_view_builds_items_with_defaults(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    view = await ConfirmationService(db_session).view(project)
    assert [(i.key, i.present, i.status) for i in view.items] == [
        ("req:FR-002", True, "unconfirmed"),
        ("user:管理者", True, "unconfirmed"),
        ("point:users", True, "unconfirmed"),
        ("point:daily_flow", True, "unconfirmed"),
        ("point:exceptions", True, "unconfirmed"),
    ]
    assert view.items[4].refs == ["FR-002"]
    assert view.items[0].status_label == "未確認"
    assert (view.adoption.unconfirmed, view.adoption.rate) == (2, None)


async def test_update_saves_fields_without_touching_documents(db_session: AsyncSession) -> None:
    """観点・却下は欄だけ変え、文書と観点の状態は変えない。"""
    project = await _project(db_session)
    service = ConfirmationService(db_session)
    await service.update(
        project,
        "point:exceptions",
        ConfirmationUpdate(opportunity="next_meeting", memo="例外を聞く"),
        updated_by=project.user_id,
    )
    view = await service.update(
        project, "req:FR-002", ConfirmationUpdate(status="rejected"), updated_by=project.user_id
    )
    point = next(i for i in view.items if i.key == "point:exceptions")
    assert (point.opportunity, point.opportunity_label, point.memo) == (
        "next_meeting",
        "次の打ち合わせ",
        "例外を聞く",
    )
    assert point.status == "unconfirmed"
    assert (view.adoption.rejected, view.adoption.rate) == (1, 0.0)
    model = await _current_model(db_session, project)
    assert model.rows[1].source == "assumed"
    states = await HearingPointRepository(db_session).load_states(project.id)
    assert states["exceptions"].status == "open"


async def test_confirming_requirement_adopts_and_records_agreement(
    db_session: AsyncSession,
) -> None:
    project = await _project(db_session)
    view = await ConfirmationService(db_session).update(
        project, "req:FR-002", ConfirmationUpdate(status="confirmed"), updated_by=project.user_id
    )
    item = next(i for i in view.items if i.key == "req:FR-002")
    # 推測でなくなって今の一覧から消えたが、確認済みとして残る
    assert (item.present, item.status, item.label) == (False, "confirmed", "FR-002 通知する")
    assert (view.adoption.confirmed, view.adoption.unconfirmed, view.adoption.rate) == (1, 1, 1.0)
    model = await _current_model(db_session, project)
    assert model.rows[1].source == "adopted"
    current = await AgreementRepository(db_session).current(project.id)
    assert [(r.key, r.origin, r.recorded_by) for r in current] == [
        ("req:FR-002", "adopt", project.user_id)
    ]


async def test_confirming_user_adopts_scene_and_records_whole_users(
    db_session: AsyncSession,
) -> None:
    project = await _project(db_session)
    view = await ConfirmationService(db_session).update(
        project, "user:管理者", ConfirmationUpdate(status="confirmed"), updated_by=project.user_id
    )
    assert next(i for i in view.items if i.key == "user:管理者").present is False
    model = await _current_model(db_session, project)
    assert [u.source for u in model.users] == ["user", "adopted"]
    current = await AgreementRepository(db_session).current(project.id)
    assert [r.key for r in current] == ["users"]
    assert current[0].content is not None
    assert [u["source"] for u in current[0].content["users"]] == ["user", "adopted"]


async def test_preview_adopt_marks_item_confirmed(db_session: AsyncSession) -> None:
    """プレビューの「採用にする」も、一覧の項目を確認済みにする(入口をそろえる)。"""
    project = await _project(db_session)
    await DocGeneratorService(db_session).adopt_requirement(
        project, "FR-002", recorded_by=project.user_id
    )
    view = await ConfirmationService(db_session).view(project)
    item = next(i for i in view.items if i.key == "req:FR-002")
    assert (item.present, item.status) == (False, "confirmed")


async def test_point_leaves_list_when_decided_in_hearing(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    service = ConfirmationService(db_session)
    await service.update(
        project, "point:exceptions", ConfirmationUpdate(memo="聞く"), updated_by=None
    )
    await HearingPointRepository(db_session).save_states(
        project.id, [PointState("exceptions", "decided", "通知しない")]
    )
    await db_session.commit()
    view = await service.view(project)
    # 未確認のまま一覧から消えた項目は出さない
    assert "point:exceptions" not in [i.key for i in view.items]


async def test_unknown_key_is_not_found(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    with pytest.raises(ConfirmationItemNotFoundError):
        await ConfirmationService(db_session).update(
            project, "req:FR-001", ConfirmationUpdate(status="confirmed"), updated_by=None
        )


async def test_simple_mode_is_not_available(db_session: AsyncSession) -> None:
    project = await _project(db_session, mode="simple")
    service = ConfirmationService(db_session)
    with pytest.raises(DesignStagesNotAvailableError):
        await service.view(project)
    with pytest.raises(DesignStagesNotAvailableError):
        await service.update(project, "req:FR-002", ConfirmationUpdate(), updated_by=None)
    # 簡易モードの「採用にする」は一覧にも合意にも書かない
    await DocGeneratorService(db_session).adopt_requirement(project, "FR-002")
    assert await ConfirmationRepository(db_session).list_for_project(project.id) == []
