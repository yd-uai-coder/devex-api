import uuid

from fastapi import APIRouter, BackgroundTasks, status

from app.api.deps import CurrentProjectDep, CurrentUserDep, RedisDep, SessionDep
from app.core.database import AsyncSessionLocal
from app.schemas.review import ReviewGate, ReviewRunRead
from app.services.review_service import ReviewService

# UML・段階と同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/reviews", tags=["reviews"])


async def execute_review(run_id: uuid.UUID) -> None:
    """`BackgroundTasks`から呼ぶレビューの実行(セッションは自前で開く ──
    doc_generator_service.generate_documents と同じ理由)。"""
    async with AsyncSessionLocal() as session:
        await ReviewService(session).execute(run_id)


@router.get("/{gate}", response_model=ReviewRunRead | None)
async def get_latest_review(
    gate: ReviewGate, session: SessionDep, current_project: CurrentProjectDep
) -> ReviewRunRead | None:
    """区切りの最新のレビューの回(指摘付き)。まだ無ければ null。"""
    service = ReviewService(session)
    run = await service.latest_documents_review(current_project)
    if run is None:
        return None
    read = ReviewRunRead.model_validate(run)
    read.is_current = run.targets == await service.document_targets(current_project)
    return read


@router.post("/{gate}", response_model=ReviewRunRead, status_code=status.HTTP_202_ACCEPTED)
async def request_review(
    gate: ReviewGate,
    session: SessionDep,
    redis: RedisDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> ReviewRunRead:
    """区切りのレビューを流し直す(「もう一度レビュー」)。受け付けた回(実行中)を返し、実行は
    バックグラウンドで行う。使えない設定は403、生成中・実行中・文書が無いは409、APIキーが無いは
    502、1日の上限は429。"""
    run = await ReviewService(session, redis=redis).start_documents_review(
        current_project, current_user, trigger="manual"
    )
    background_tasks.add_task(execute_review, run.id)
    read = ReviewRunRead.model_validate(run)
    read.is_current = True
    return read
