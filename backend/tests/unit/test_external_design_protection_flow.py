"""外部設計書の再生成で、合意済みの画面・API・外部連携を守る流れのテスト。

SUT: DocGeneratorService.generate の外部設計書の生成(`_generate_external_design`・`_rewritable`。
     app/services/doc_generator_service.py)、CR で書き換えてよい要素(rewritable_keys。
     app/change_requests/prediction.py)、スキーマ GeneratedDocumentRead(document_read)の
     `protection`
ドライバ: 各テスト関数
スタブ: DB はインメモリ SQLite(`db_session`)を本物のリポジトリで使う。設計は段階1〜8 を承認済みに
した fixture(`create_impact_project`。画面 SCR-001・API の POST と GET)。文書の生成の LLM は
FakeLLM(外部設計書の見本 = 画面 SCR-001 と POST だけを、束と違う中身で返し、プロンプトを記録する)
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.change_requests import NEW_TEXT, agreed_change_request_project
from tests.fixtures.external_design import external_design_output, upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.requirements import requirements_output

from app.ai.llm.paraphrase import paraphrase_external_design
from app.change_requests import rewritable_keys, targets_from_json
from app.documents.external_design.drafting import (
    ExternalDesignGenerationOutput,
    GeneratedIntegration,
)
from app.documents.external_design.model import ExternalDesignModel
from app.documents.requirements.drafting import AGREED_MARK
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.repositories.change_request import ChangeRequestRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.baseline import BaselineCreate
from app.schemas.document import RestoredRead
from app.services.baseline_service import BaselineService
from app.services.doc_generator_service import DocGeneratorService, document_read

_AT = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
_DIAGNOSIS = "## 自己診断\n最重要: 特になし"


async def _regenerate(
    session: AsyncSession,
    project: Project,
    external: ExternalDesignGenerationOutput,
    *,
    cr_number: int | None = None,
) -> FakeLLM:
    """要件定義書から作り直す(要件定義書は CR の対象の FR-001 だけを書く見本)。"""
    requirements = requirements_output(
        prev_id="FR-001", text=NEW_TEXT if cr_number else "予約を登録できる"
    )
    llm = FakeLLM(
        content_sequence=[_DIAGNOSIS, _DIAGNOSIS],
        structured_by_schema=upstream_outputs(requirements, external),
    )
    service = DocGeneratorService(session)
    status = await service.request_generation(
        project, start_from="requirements", cr_number=cr_number
    )
    await service.generate(
        project.id,
        project.user_id,
        status_before_generation=status,
        start_from="requirements",
        cr_number=cr_number,
        llm=llm,
    )
    return llm


async def _current(session: AsyncSession, project: Project) -> GeneratedDocument:
    document = await GeneratedDocumentRepository(session).get_current(
        project_id=project.id, doc_type="external_design"
    )
    assert document is not None and document.model is not None
    return document


def _model(document: GeneratedDocument) -> ExternalDesignModel:
    return ExternalDesignModel.model_validate(document.model)


async def test_smoke_rewritten_and_dropped_elements_go_back_to_the_baseline(
    db_session: AsyncSession,
) -> None:
    """束 B-1 の後の、CR を渡さない再生成。LLM が画面と POST を書き換え、GET を落としても、
    合意の形と並びに戻り、戻した要素が版と応答に残る。合意の無い外部連携は残る。"""
    project = await create_impact_project(db_session)
    await BaselineService(db_session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    before = _model(await _current(db_session, project))
    output = external_design_output()
    output.integrations = [
        GeneratedIntegration(service="メール配信", purpose="通知", auth="", timing="", req_ids=[])
    ]

    llm = await _regenerate(db_session, project, output)

    current = await _current(db_session, project)
    model = _model(current)
    assert model.screens == before.screens
    assert model.apis == before.apis
    assert [i.service for i in model.integrations] == ["メール配信"]
    assert current.protection == [
        {"key": "screen:SCR-001", "reason": "changed"},
        {"key": "api:POST /api/v1/reservations", "reason": "changed"},
        {"key": "api:GET /api/v1/reservations", "reason": "dropped"},
    ]
    assert "GET" in current.content
    assert document_read(current).protection == [
        RestoredRead(key="screen:SCR-001", reason="changed"),
        RestoredRead(key="api:POST /api/v1/reservations", reason="changed"),
        RestoredRead(key="api:GET /api/v1/reservations", reason="dropped"),
    ]
    # 外部設計書のプロンプト(2つ目の構造化出力)で、合意済みの前の版の行に印が付く
    prompt = str(llm.structured_messages[1][-1].content)
    assert "## 前の版の外部連携\nなし" in prompt
    assert sum(line.endswith(f"|{AGREED_MARK}") for line in prompt.splitlines()) == 3


async def test_paraphrased_service_of_agreed_integration_goes_back_once(
    db_session: AsyncSession,
) -> None:
    """束で合意した外部連携のサービス名を、再生成の LLM が言い換えても(偽の LLM の言い換えの
    規則)、合意の名前の1行だけが残る。"""
    project = await create_impact_project(db_session)
    first = external_design_output()
    first.integrations = [
        GeneratedIntegration(service="地図 API", purpose="位置", auth="", timing="", req_ids=[])
    ]
    await _regenerate(db_session, project, first)
    await BaselineService(db_session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )

    await _regenerate(db_session, project, paraphrase_external_design(first))

    current = await _current(db_session, project)
    assert [i.service for i in _model(current).integrations] == ["地図 API"]
    assert {"key": "integration:地図 API", "reason": "dropped"} in (current.protection or [])


async def test_change_request_may_rewrite_only_predicted_elements(
    db_session: AsyncSession,
) -> None:
    """CR-1(FR-001)を反映する再生成。保存した予測の画面 SCR-001 と POST は書き換えてよく、
    予測に無い GET は戻す。"""
    project = await agreed_change_request_project(db_session)
    change_request = await ChangeRequestRepository(db_session).get_by_number(
        project_id=project.id, number=1
    )
    assert change_request is not None
    assert rewritable_keys(
        targets_from_json(change_request.targets), change_request.predicted_impact
    ) == {"req:FR-001", "screen:SCR-001", "api:POST /api/v1/reservations"}

    await _regenerate(db_session, project, external_design_output(), cr_number=1)

    current = await _current(db_session, project)
    model = _model(current)
    assert model.screens[0].req_ids == ["FR-001"]  # LLM が書いたまま
    assert [a.label for a in model.apis] == [
        "POST /api/v1/reservations",
        "GET /api/v1/reservations",
    ]
    assert model.apis[0].summary == "予約を登録する"
    assert current.protection == [{"key": "api:GET /api/v1/reservations", "reason": "dropped"}]


async def test_simple_mode_does_not_protect(db_session: AsyncSession) -> None:
    """簡易ドキュメントモードは合意を読まず守らない(版の`protection`は NULL)。"""
    project = await create_impact_project(db_session)
    await BaselineService(db_session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    project.mode = "simple"
    await db_session.commit()

    await _regenerate(db_session, project, external_design_output())

    current = await _current(db_session, project)
    assert [a.label for a in _model(current).apis] == ["POST /api/v1/reservations"]
    assert current.protection is None
