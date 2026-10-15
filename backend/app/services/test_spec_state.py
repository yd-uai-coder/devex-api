"""テスト仕様書の今の版と「古いか」(読み出しの API と詳細設計書の zip が共用する)。

「古い」は、生成した時点の入力の版(model の`inputs`)が今の入力の版と違うこと。入力は、簡易は
4文書、詳細は要件定義書・外部設計書と段階1・段階7 の承認した版(承認されていない・古い段階は None)。
"""

from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.detailed_design.plan import PLAN_STAGE
from app.detailed_design.stages import doc_key, stage_key
from app.documents.test_spec import TestSpecModel, is_outdated
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.repositories.generated_document import (
    DOC_TYPES_BY_MODE,
    TEST_SPEC,
    GeneratedDocumentRepository,
)
from app.services.design_stage_service import DesignStageService

# 詳細設計モードのテスト仕様書が読む段階(処理の一覧と実装計画。処理・単位の列を引く)
FUNCTION_STAGE = 1
TEST_SPEC_STAGES = (FUNCTION_STAGE, PLAN_STAGE)


@dataclass(frozen=True)
class CurrentTestSpec:
    """テスト仕様書の今の版と、その model・古いか。model の無い・壊れた版は空の model・古くない。"""

    document: GeneratedDocument
    model: TestSpecModel
    outdated: bool


async def spec_inputs(session: AsyncSession, project: Project) -> dict[str, int | None]:
    """テスト仕様書の入力の今の版(簡易は4文書、詳細は要件・外部と段階1・段階7 の承認した版。
    承認されていない・古い段階は None)。"""
    documents = GeneratedDocumentRepository(session)
    inputs: dict[str, int | None] = {}
    for doc_type in DOC_TYPES_BY_MODE[project.mode]:
        current = await documents.get_current(project_id=project.id, doc_type=doc_type)
        inputs[doc_key(doc_type)] = current.version if current is not None else None
    if project.mode == "detailed":
        approved = await DesignStageService(session).approved_stages(project, TEST_SPEC_STAGES)
        for stage in TEST_SPEC_STAGES:
            inputs[stage_key(stage)] = approved[stage][0] if stage in approved else None
    return inputs


async def current_test_spec(session: AsyncSession, project: Project) -> CurrentTestSpec | None:
    """テスト仕様書の今の版と、古いか。まだ無ければ None。"""
    document = await GeneratedDocumentRepository(session).get_current(
        project_id=project.id, doc_type=TEST_SPEC
    )
    if document is None:
        return None
    try:
        model = TestSpecModel.model_validate(document.model or {})
    except ValidationError:
        model = TestSpecModel()
    return CurrentTestSpec(
        document=document,
        model=model,
        outdated=is_outdated(model, await spec_inputs(session, project)),
    )
