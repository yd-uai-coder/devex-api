"""変更要求(CR)の流れのテスト用の部品: 段階1〜8 承認済みの設計に、合意済みの版 B-1 と、FR-001 を
対象にする CR-1 を作り、文書の再生成で反映する(偽の LLM)。

再検証・レビューを結ぶ・束に入れる・時系列のテストが、反映の後の CR から始めるために使う。
`confirm_everything`は、検証の前に印と文書の側の確認をすべて済ませる。
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.requirements import requirements_output

from app.models.project import Project
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    DocumentChecksUpdate,
)
from app.services.baseline_service import BaselineService
from app.services.change_request_service import ChangeRequestService
from app.services.design_stage_service import DesignStageService
from app.services.doc_generator_service import DocGeneratorService

AGREED_AT = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
NEW_TEXT = "予約を登録し、確認メールを送る"


async def agreed_change_request_project(session: AsyncSession) -> Project:
    """段階1〜8 承認済みの設計・合意済みの版 B-1・FR-001 を対象にする合意した CR-1。"""
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=AGREED_AT, reason="合意"), recorded_by=None
    )
    service = ChangeRequestService(session)
    await service.create(
        project,
        ChangeRequestCreate.model_validate(
            {
                "title": "登録で確認メールを送る",
                "content": "予約の登録の後に確認メールを送る",
                "background_source": "meeting",
                "targets": [
                    {"requirement_id": "FR-001", "kind": "content", "proposed_text": NEW_TEXT}
                ],
            }
        ),
        created_by=None,
    )
    await service.explain(project, 1, actor_id=None)
    await service.decide(
        project,
        1,
        ChangeRequestDecision.model_validate(
            {"decision": "agreed", "decided_at": AGREED_AT, "reason": "合意"}
        ),
        actor_id=None,
    )
    return project


async def reflect_change_request(session: AsyncSession, project: Project) -> None:
    """CR-1 を渡して要件定義書から作り直す(反映済みになり、印が付く)。"""
    llm = FakeLLM(
        content_sequence=["## 自己診断\n最重要: 特になし", "## 自己診断\n最重要: 特になし"],
        structured_by_schema=upstream_outputs(
            requirements_output(prev_id="FR-001", text=NEW_TEXT)
        ),
    )
    status = await DocGeneratorService(session).request_generation(
        project, start_from="requirements", cr_number=1
    )
    await DocGeneratorService(session).generate(
        project.id,
        project.user_id,
        status_before_generation=status,
        start_from="requirements",
        cr_number=1,
        llm=llm,
    )


async def applied_change_request_project(session: AsyncSession) -> Project:
    """反映済みの CR-1 を持つプロジェクト。"""
    project = await agreed_change_request_project(session)
    await reflect_change_request(session, project)
    return project


async def confirm_everything(session: AsyncSession, project: Project, number: int = 1) -> None:
    """反映で付いた印をすべて確認済みにし、文書の側の確認の項目もすべて確認済みにする。"""
    service = ChangeRequestService(session)
    view = await service.verification(project, number)
    stages = DesignStageService(session)
    for stage in {m.stage for m in view.verification.marks}:
        keys = [m.key for m in view.verification.marks if m.stage == stage]
        await stages.review_marks(project, stage=stage, keys=keys, reviewed=True)
    keys = [d.key for d in view.verification.documents]
    if keys:
        await service.check_documents(project, number, DocumentChecksUpdate(keys=keys))
