from fastapi import APIRouter

from app.api.deps import CurrentProjectDep, CurrentUserDep, SessionDep
from app.schemas.confirmation import ConfirmationListRead, ConfirmationUpdate
from app.services.confirmation_service import ConfirmationService

# 合意と同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/confirmations", tags=["confirmations"])


@router.get("", response_model=ConfirmationListRead)
async def list_confirmations(
    session: SessionDep, current_project: CurrentProjectDep
) -> ConfirmationListRead:
    """確認事項の一覧(推測の要件・推測の利用者・根拠が未決の観点)と推測の採用率を返す(LLMは
    呼ばない)。簡易ドキュメントモードは409。"""
    return await ConfirmationService(session).view(current_project)


@router.patch("/{key:path}", response_model=ConfirmationListRead)
async def update_confirmation(
    key: str,
    payload: ConfirmationUpdate,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ConfirmationListRead:
    """確認事項1件の決める機会・状態・メモを変え、作り直した一覧を返す。推測の要件・推測の利用者
    を確認済みにすると、採用にして合意を記録する。項目が無ければ404、簡易ドキュメントモードは409。"""
    return await ConfirmationService(session).update(
        current_project, key, payload, updated_by=current_user.id
    )
