from fastapi import APIRouter

from app.api.deps import CurrentUserDep, SessionDep
from app.repositories.prompt_template import PromptTemplateRepository
from app.schemas.prompt_template import PromptTemplateRead

router = APIRouter(prefix="/prompt-templates", tags=["prompt-templates"])


@router.get("", response_model=list[PromptTemplateRead])
async def list_prompt_templates(
    session: SessionDep, current_user: CurrentUserDep
) -> list[PromptTemplateRead]:
    """選択可能なプロンプトテンプレート一覧(固定シードデータ)を取得する。
    プロジェクトに紐づかない一覧のため、CurrentProjectDepではなくCurrentUserDepのみで認証する。"""
    templates = await PromptTemplateRepository(session).list_all_templates()
    return [PromptTemplateRead.model_validate(t) for t in templates]
