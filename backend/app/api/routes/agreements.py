from fastapi import APIRouter

from app.api.deps import CurrentProjectDep, SessionDep
from app.schemas.agreement import AgreementRead, agreement_read
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
