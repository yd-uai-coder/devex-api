from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentProjectDep, SessionDep
from app.schemas.agreement import (
    AgreementDocType,
    AgreementHistoryRead,
    AgreementRead,
    AgreementStatusRead,
    agreement_history_read,
    agreement_read,
    agreement_status_read,
)
from app.services.agreement_service import AgreementService

# 束・CR と同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/agreements", tags=["agreements"])


@router.get("", response_model=list[AgreementRead])
async def list_agreements(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[AgreementRead]:
    """今の合意(要素ごとの最新の合意の記録)を返す。簡易ドキュメントモードは409。"""
    records = await AgreementService(session).current(current_project)
    return [agreement_read(record) for record in records]


@router.get("/history", response_model=AgreementHistoryRead)
async def get_agreement_history(
    session: SessionDep,
    current_project: CurrentProjectDep,
    key: Annotated[str, Query(min_length=1, max_length=300)],
) -> AgreementHistoryRead:
    """要素1つ(`key`。`req:FR-001`・`users`・`screen:SCR-001`・`api:GET /x` など)の履歴。キーに
    空白・`/` を含むため、パスでなくクエリで受ける。合意の対象でないキーは422、簡易ドキュメント
    モードは409。"""
    history = await AgreementService(session).history(current_project, key)
    return agreement_history_read(history)


@router.get("/status", response_model=AgreementStatusRead)
async def get_agreement_status(
    session: SessionDep, current_project: CurrentProjectDep, doc_type: AgreementDocType
) -> AgreementStatusRead:
    """要件定義書・外部設計書(`doc_type`)の表示中の版の、要素ごとの合意の状態(合意済み・合意と
    違う・未合意・今の版に無い)と、その版で合意の形へ戻した要素。文書が無ければ空(`version`は
    null)。ほかの文書の種類は422、簡易ドキュメントモードは409。"""
    status = await AgreementService(session).status(current_project, doc_type)
    return agreement_status_read(status)
