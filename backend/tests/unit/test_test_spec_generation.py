"""テスト仕様書の生成と読み出しのテスト。

SUT: DocGeneratorService(request_generation / generate / get_test_spec)と、ルート
     (get_test_spec)。
ドライバ: 各テスト関数
スタブ: FakeLLM(4文書・テスト仕様書の構造化出力と自己診断)、詳細設計モードは E2E 用の偽 LLM
     (`E2eFakeLLM`。段階1〜7 を画面と同じ順に作る)。DB はインメモリの SQLite(作った版・入力の
     版の写しそのものが検証対象のため、スタブにしない)。
"""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.fake_llm_flow import (
    create_fake_detailed_project,
    generate_and_approve_stages_1_to_7,
)

from app.ai.llm.fake import E2eFakeLLM
from app.api.routes.projects import get_test_spec
from app.documents.test_spec import TestSpecModel, validate_test_spec
from app.models.chat_history import ChatHistory
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import TEST_SPEC, GeneratedDocumentRepository
from app.services import llm_retry
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import DocumentNotFoundError, TestSpecUpstreamNotReadyError


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_retry, "RETRY_DELAY_SECONDS", 0)


def _llm(**outputs: Any) -> FakeLLM:
    diagnoses: list[Any] = ["## 自己診断"] * 3
    return FakeLLM(content_sequence=diagnoses, structured_by_schema=upstream_outputs(**outputs))


async def _simple_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="simple")
    session.add(project)
    await session.flush()
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    await session.commit()
    return project


async def _test_spec(session: AsyncSession, project: Project) -> TestSpecModel:
    document = await GeneratedDocumentRepository(session).get_current(
        project_id=project.id, doc_type=TEST_SPEC
    )
    assert document is not None
    return TestSpecModel.model_validate(document.model)


# --- 簡易ドキュメントモード: 4文書に続けて作る ---


async def test_simple_generation_makes_test_spec_after_four_documents(
    db_session: AsyncSession,
) -> None:
    project = await _simple_project(db_session)
    llm = _llm()

    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=llm)

    model = await _test_spec(db_session, project)
    assert [c.id for c in model.integration] == ["IT-001", "IT-002"]
    # 処理・単位は内部設計書の処理別データフローと実装計画書のタスクから引く
    assert model.integration[0].functions == ["DF-1"]
    assert model.integration[0].units == ["M-01-T01"]
    assert model.inputs == {
        "doc:requirements": 1,
        "doc:external_design": 1,
        "doc:internal_design": 1,
        "doc:implementation_plan": 1,
    }
    # 入力は要件定義書の md と、コードが決めた必要なテストの表
    prompt = str(llm.structured_messages[-1][-1].content)
    assert "## 必要なテスト\n種類|対象|観点|理由|要件|画面" in prompt
    assert "結合|POST /api/v1/reservations|正常|" in prompt


async def test_self_diagnosis_does_not_read_test_spec(db_session: AsyncSession) -> None:
    project = await _simple_project(db_session)
    llm = _llm()

    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=llm)

    [diagnosis_messages] = llm.invoke_messages
    assert "# テスト仕様書" not in str(diagnosis_messages[-1].content)
    assert "# 4. 実装計画書" in str(diagnosis_messages[-1].content)


async def test_test_spec_failure_rolls_back_the_set(db_session: AsyncSession) -> None:
    project = await _simple_project(db_session)
    outputs = upstream_outputs()
    outputs.pop(next(t for t in outputs if t.__name__ == "TestSpecGenerationOutput"))
    llm = FakeLLM(content_sequence=["## 自己診断"], structured_by_schema=outputs)

    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=llm)

    documents = await GeneratedDocumentRepository(db_session).list_current_for_project(project.id)
    assert documents == []
    assert project.status != "generating"


async def test_get_test_spec_reports_counts_issues_and_outdated(db_session: AsyncSession) -> None:
    project = await _simple_project(db_session)
    service = DocGeneratorService(db_session)
    with pytest.raises(DocumentNotFoundError):
        await get_test_spec(db_session, project)

    await service.generate(project.id, project.user_id, llm=_llm())
    read = await get_test_spec(db_session, project)
    assert read.document.doc_type == TEST_SPEC
    assert read.document.content.startswith("# テスト仕様書")
    assert read.counts == {"integration": 2, "system": 0}
    assert read.outdated is False
    assert read.design_gaps == 0
    assert [i.code for i in read.issues] == []

    # 2回目の生成の後で要件定義書を前の版に戻すと、テスト仕様書の入力が今と違う
    await service.generate(project.id, project.user_id, llm=_llm())
    await service.restore_version(project.id, "requirements", 1)
    assert (await service.get_test_spec(project)).outdated is True


# --- 詳細設計モード: 段階7の承認の後に起点で作る ---


async def test_detailed_needs_approved_stages_1_and_7(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)

    with pytest.raises(TestSpecUpstreamNotReadyError):
        await DocGeneratorService(db_session).request_generation(project, start_from=TEST_SPEC)
    assert project.status == "completed"


async def test_detailed_test_spec_from_fake_llm_has_no_major_issues(
    db_session: AsyncSession,
) -> None:
    """E2E 用の偽 LLM の契約: 段階1〜7 を作って承認した後のテスト仕様書が、検証で
    critical / major を出さない。処理・単位は段階1・段階7 から引く。"""
    llm = E2eFakeLLM()
    project = await create_fake_detailed_project(db_session, llm)
    await generate_and_approve_stages_1_to_7(db_session, project, llm)
    service = DocGeneratorService(db_session)
    history_before = len(await ChatHistoryRepository(db_session).list_for_project(project.id))

    before = await service.request_generation(project, start_from=TEST_SPEC)
    await service.generate(
        project.id, project.user_id, status_before_generation=before, start_from=TEST_SPEC,
        llm=llm,
    )

    model = await _test_spec(db_session, project)
    assert [i for i in validate_test_spec(model) if i.level != "minor"] == []
    create = next(c for c in model.integration if c.target == "POST /api/v1/reservations")
    assert create.functions == ["F-01"] and create.units
    assert set(model.inputs) == {
        "doc:requirements",
        "doc:external_design",
        "stage:1",
        "stage:7",
    }
    assert None not in model.inputs.values()
    # テスト仕様書だけのときは自己診断も設計レビューも流さない(チャットは増えない)
    history: list[ChatHistory] = await ChatHistoryRepository(db_session).list_for_project(
        project.id
    )
    assert len(history) == history_before
    assert project.status == "completed"
    assert (await service.get_test_spec(project)).outdated is False
