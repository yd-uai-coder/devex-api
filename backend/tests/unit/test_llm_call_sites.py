"""チャット・生成・参考資料の取り込みが、持ち主と用途で LLM を解決することのテスト(統合スモーク)。

SUT: ChatService(最初の発話)・DocGeneratorService.generate・DesignStageGenerationService.execute・
     ProjectService.create(PDF の取り込み)が、テストの差し替え(`llm=`)が無いときに
     LlmResolver で解決する経路
ドライバ: 各テスト関数(サービスのメソッドを `llm=` なしで呼ぶ)
スタブ: LlmResolver.resolve をフェイクに差し替える(tests/fixtures/llm_resolver.py。呼ばれた
     ユーザーと用途を記録する)。返す LLM は E2E 用の偽物(E2eFakeLLM)と FakeLLM。
     DB はインメモリ SQLite
"""

from collections.abc import Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.llm_resolver import stub_llm_resolver

from app.ai.llm.catalog import Provider, Purpose
from app.ai.llm.fake import E2eFakeLLM
from app.models.project import Project
from app.models.user import User
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.intake_file import IntakeFileRepository
from app.services.chat_service import ChatService
from app.services.design_stage_generation_service import DesignStageGenerationService
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import LlmUnavailableError
from app.services.llm_resolver import LlmResolver, ResolvedLlm
from app.services.project import ProjectService, UploadedFileInput


async def _simple_project(
    session: AsyncSession, files: Sequence[UploadedFileInput] = ()
) -> Project:
    user = User(email="call-sites@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    return await ProjectService(session).create(
        user_id=user.id,
        name="在庫管理",
        intake={"system_overview": "在庫管理システム", "goals_raw": "在庫を可視化したい"},
        files=list(files),
    )


async def test_opening_reply_and_generation_resolve_by_owner_and_purpose(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = stub_llm_resolver(monkeypatch, E2eFakeLLM())
    project = await _simple_project(db_session)

    await ChatService(db_session).generate_opening_reply(project)
    await DocGeneratorService(db_session).generate(project.id, project.user_id)

    purposes = {purpose for _, purpose in resolver.calls}
    assert purposes == {Purpose.CHAT, Purpose.GENERATION}
    assert all(user_id == project.user_id for user_id, _ in resolver.calls)
    docs = await GeneratedDocumentRepository(db_session).list_current_for_project(project.id)
    assert docs  # 解決した LLM で設計書ができた


async def test_stage_generation_records_failure_when_llm_is_unavailable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """段階の生成で LLM を解決できなければ、段階を「生成中」のまま残さず失敗にする。"""
    project = await create_detailed_project(db_session)
    await db_session.commit()
    service = DesignStageGenerationService(db_session)
    await service.request_generation(project, stage=1)
    resolver = stub_llm_resolver(monkeypatch, error=LlmUnavailableError("キーが無い"))
    project_id, user_id = project.id, project.user_id  # rollback で失効した後に読まない

    await service.execute(project_id=project_id, user_id=user_id, stage=1)

    assert resolver.calls == [(user_id, Purpose.GENERATION)]
    row = await DesignStageRepository(db_session).get(project_id=project_id, stage=1)
    assert row is not None and row.generation_status == "failed"


async def test_pdf_is_read_with_owner_gemini(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list = []

    async def _reader(_self: LlmResolver, user_id) -> ResolvedLlm:
        called.append(user_id)
        return ResolvedLlm(
            llm=FakeLLM(content="PDFの本文"),
            provider=Provider.GEMINI,
            model="gemini-3.5-flash-lite",
            own_key=False,
        )

    monkeypatch.setattr(LlmResolver, "resolve_pdf_reader", _reader)

    project = await _simple_project(
        db_session, [UploadedFileInput(filename="spec.pdf", data=b"%PDF-1.4 ...")]
    )

    [intake_file] = await IntakeFileRepository(db_session).list_for_project(project.id)
    assert called == [project.user_id]
    assert (intake_file.status, intake_file.extracted_text) == ("processed", "PDFの本文")


async def test_pdf_fails_softly_when_gemini_is_unavailable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _reader(_self: LlmResolver, _user_id) -> ResolvedLlm:
        raise LlmUnavailableError("キーが無い")

    monkeypatch.setattr(LlmResolver, "resolve_pdf_reader", _reader)

    project = await _simple_project(
        db_session, [UploadedFileInput(filename="spec.pdf", data=b"%PDF-1.4 ...")]
    )

    [intake_file] = await IntakeFileRepository(db_session).list_for_project(project.id)
    assert intake_file.status == "failed"
    assert intake_file.error_message == "PDFを読むAIを使えませんでした"
