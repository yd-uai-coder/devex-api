"""要素1つの履歴の API(サービス・ルート)のテスト。

SUT: AgreementService.history(app/services/agreement_service.py)、
     AgreementRepository.list_for_key・GeneratedDocumentRepository.list_protected・
     list_pruned_protections と create_version の巡回での写し(PrunedProtection)、
     スキーマ AgreementHistoryRead・agreement_history_read(app/schemas/agreement.py)、
     ルート get_agreement_history(app/api/routes/agreements.py)、AgreementKeyInvalidError
ドライバ: 各テスト関数
スタブ: DB はインメモリ SQLite(`db_session`)を本物のリポジトリで使う。CR の反映までは
tests/fixtures/change_requests.py(文書の生成の LLM は FakeLLM)。合意の形へ戻した版は、
リポジトリで`protection`を付けた版を直接作る(戻しそのものは test_agreement_protection.py が見る)
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project, impact_documents
from tests.fixtures.change_requests import NEW_TEXT, applied_change_request_project

from app.api.routes.agreements import get_agreement_history
from app.models.project import Project
from app.models.pruned_protection import PrunedProtection
from app.repositories.agreement import AgreementRepository
from app.repositories.generated_document import (
    MAX_VERSIONS_PER_DOC_TYPE,
    GeneratedDocumentRepository,
)
from app.schemas.agreement import AgreementHistoryRead
from app.services.agreement_service import AgreementService
from app.services.errors import AgreementKeyInvalidError, DesignStagesNotAvailableError


async def test_history_of_a_requirement_changed_by_change_request(
    db_session: AsyncSession,
) -> None:
    """B-1 で合意 → CR-1 の反映で中身が変わった。CR-1 は対象(内容の変更)として出る。"""
    project = await applied_change_request_project(db_session)

    history = await AgreementService(db_session).history(project, "req:FR-001")

    assert [(e.event, e.origin_label) for e in history.entries] == [
        ("agreed", "B-1"),
        ("changed", "CR-1"),
    ]
    assert history.current is not None and history.current["text"] == NEW_TEXT
    assert [(c.number, c.target_kind, c.status) for c in history.change_requests] == [
        (1, "content", "applied")
    ]
    records = await AgreementRepository(db_session).list_for_key(project.id, "req:FR-001")
    assert [r.seq for r in records] == sorted(r.seq for r in records)


async def test_history_includes_restored_versions(db_session: AsyncSession) -> None:
    """合意の形へ戻した版(`protection`にキーがある版)が、戻しの行として入る。"""
    project = await applied_change_request_project(db_session)
    documents = GeneratedDocumentRepository(db_session)
    await documents.create_version(
        project_id=project.id,
        doc_type="requirements",
        content="# r",
        model=impact_documents()["requirements"],
        protection=[{"key": "req:FR-002", "reason": "dropped"}],
    )
    await db_session.commit()

    protected = await documents.list_protected(project.id, ("requirements", "external_design"))
    assert protected and all(isinstance(d.protection, list) for d in protected)
    history = await AgreementService(db_session).history(project, "req:FR-002")

    # CR-1 の反映の再生成(偽の LLM は FR-002 を書かない)でも戻している。並びは日時の順
    # (SQLite の版の日時は秒の単位なので、同じ秒の記録との前後はここでは見ない)
    assert sorted((e.event, e.change_request_number or 0) for e in history.entries) == [
        ("agreed", 0),
        ("restored", 0),
        ("restored", 1),
    ]
    assert [e.occurred_at for e in history.entries] == sorted(
        e.occurred_at for e in history.entries
    )
    restored = next(
        e for e in history.entries if e.event == "restored" and e.change_request_number is None
    )
    assert (restored.doc_type, restored.restore_reason) == ("requirements", "dropped")
    assert restored.version == max(d.version for d in protected if d.doc_type == "requirements")


async def test_restore_of_a_pruned_version_stays_in_history(db_session: AsyncSession) -> None:
    """巡回で消えた版の戻しは、写しの表に残り、履歴に出る。戻しの無い版・空の戻しの版は
    写さない。"""
    project = await create_impact_project(db_session)
    documents = GeneratedDocumentRepository(db_session)
    model = impact_documents()["requirements"]
    protections = [[{"key": "req:FR-002", "reason": "dropped"}], [], None]
    for protection in protections:
        await documents.create_version(
            project_id=project.id,
            doc_type="requirements",
            content="# r",
            model=model,
            protection=protection,
        )
    pruned_version = (await documents.list_protected(project.id, ("requirements",)))[0].version
    for _ in range(MAX_VERSIONS_PER_DOC_TYPE):
        await documents.create_version(
            project_id=project.id, doc_type="requirements", content="# r", model=model
        )
    await db_session.commit()

    remaining = await documents.list_versions(project_id=project.id, doc_type="requirements")
    assert pruned_version not in [d.version for d in remaining]
    pruned = await documents.list_pruned_protections(project.id, ("requirements",))
    assert [(p.doc_type, p.version, p.protection) for p in pruned] == [
        ("requirements", pruned_version, [{"key": "req:FR-002", "reason": "dropped"}])
    ]
    assert all(isinstance(p, PrunedProtection) for p in pruned)

    history = await AgreementService(db_session).history(project, "req:FR-002")
    restored = [e for e in history.entries if e.event == "restored"]
    assert [(e.version, e.restore_reason, e.change_request_number) for e in restored] == [
        (pruned_version, "dropped", None)
    ]


async def test_route_returns_the_history_read(db_session: AsyncSession) -> None:
    project = await applied_change_request_project(db_session)

    read = await get_agreement_history(db_session, project, "screen:SCR-001")

    assert isinstance(read, AgreementHistoryRead)
    assert read.kind == "screen" and read.entries[0].event == "agreed"
    assert read.entries[0].origin_label == "B-1"


async def test_unagreed_key_has_empty_history(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)

    read = await get_agreement_history(db_session, project, "req:FR-009")

    assert (read.label, read.current, read.entries, read.change_requests) == (
        "req:FR-009",
        None,
        [],
        [],
    )


async def test_invalid_key_and_simple_mode(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    with pytest.raises(AgreementKeyInvalidError):
        await AgreementService(db_session).history(project, "unit:U-001")

    simple = Project(user_id=project.user_id, title="簡易", mode="simple", status="completed")
    db_session.add(simple)
    await db_session.commit()
    with pytest.raises(DesignStagesNotAvailableError):
        await AgreementService(db_session).history(simple, "req:FR-001")
