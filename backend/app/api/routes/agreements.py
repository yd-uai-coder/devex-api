from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentProjectDep, SessionDep
from app.schemas.agreement import (
    AgreementHistoryRead,
    AgreementRead,
    agreement_history_read,
    agreement_read,
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
