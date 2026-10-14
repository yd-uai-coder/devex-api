import uuid

from fastapi import APIRouter, BackgroundTasks, status

from app.api.deps import CurrentProjectDep, CurrentUserDep, RedisDep, SessionDep
from app.core.database import AsyncSessionLocal
from app.schemas.hearing import HearingPointsRead
from app.schemas.review import (
    FindingDecisionUpdate,
    FixCheckRead,
    RehearingRequest,
    ReviewFindingRead,
    ReviewGate,
    ReviewGateSummaryRead,
    ReviewRunRead,
)
from app.services.review_service import GateSummary, ReviewService

# UML・段階と同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/reviews", tags=["reviews"])


async def execute_review(run_id: uuid.UUID) -> None:
    """`BackgroundTasks`から呼ぶレビューの実行(セッションは自前で開く ──
    doc_generator_service.generate_documents と同じ理由)。"""
    async with AsyncSessionLocal() as session:
        await ReviewService(session).execute(run_id)


def _annotate(finding: ReviewFindingRead, summary: GateSummary) -> ReviewFindingRead:
    """指摘に、追跡の状態と、今の版についての「直ったか」の判定を付ける。"""
    finding.followup = summary.followups.get(finding.id)
    check = summary.fix_checks.get(finding.id)
    finding.fix_check = FixCheckRead.model_validate(check) if check is not None else None
    return finding


def _summary_read(summary: GateSummary) -> ReviewGateSummaryRead:
    run = None
    if summary.run is not None:
        run = ReviewRunRead.model_validate(summary.run)
        run.is_current = summary.is_current
        for finding in run.findings:
            _annotate(finding, summary)
    adopted_before = [
        _annotate(ReviewFindingRead.model_validate(f), summary) for f in summary.adopted_before
    ]
    return ReviewGateSummaryRead(
        gate=summary.gate,
        ready=summary.ready,
        status=summary.status,
        run=run,
        adopted_before=adopted_before,
    )


@router.get("", response_model=list[ReviewGateSummaryRead])
async def list_review_gates(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[ReviewGateSummaryRead]:
    """モードの区切りごとの状態と最新の回(簡易は文書だけ、詳細は文書・段階6・段階7)。"""
    summaries = await ReviewService(session).gate_summaries(current_project)
    return [_summary_read(summary) for summary in summaries]


@router.put("/findings/{finding_id}", response_model=ReviewFindingRead)
async def decide_finding(
    finding_id: uuid.UUID,
    payload: FindingDecisionUpdate,
    session: SessionDep,
    current_project: CurrentProjectDep,
) -> ReviewFindingRead:
    """指摘の採否(採用・却下・保留・未判断)を変える。プロジェクトに無い指摘は404。"""
    service = ReviewService(session)
    finding = await service.decide(current_project, finding_id, payload.decision)
    read = ReviewFindingRead.model_validate(finding)
    read.followup = await service.followup_of(current_project, finding)
    return read


@router.post("/rehearing", response_model=HearingPointsRead)
async def request_rehearing(
    payload: RehearingRequest, session: SessionDep, current_project: CurrentProjectDep
) -> HearingPointsRead:
    """採用したヒアリングの指摘の観点を未決に戻し、チャットで聞き直す(最初の問いかけはコードが
    組み立てる)。作り直した観点の表を返す。"""
    return await ReviewService(session).rehearing(current_project, payload.finding_ids)


@router.get("/{gate}", response_model=ReviewRunRead | None)
async def get_latest_review(
    gate: ReviewGate, session: SessionDep, current_project: CurrentProjectDep
) -> ReviewRunRead | None:
    """区切りの最新のレビューの回(指摘付き)。まだ無ければ null。"""
    service = ReviewService(session)
    run = await service.latest(current_project, gate)
    if run is None:
        return None
    read = ReviewRunRead.model_validate(run)
    read.is_current = run.targets == await service.targets_for(current_project, gate)
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
    """区切りのレビューを流す(段階6・7の「レビューする」・「もう一度レビュー」)。受け付けた回
    (実行中)を返し、実行はバックグラウンドで行う。使えない設定は403、生成中・実行中・区切りが
    そろっていないは409、APIキーが無いは502、1日の上限は429。"""
    run = await ReviewService(session, redis=redis).start_review(
        current_project, current_user, gate=gate, trigger="manual"
    )
    background_tasks.add_task(execute_review, run.id)
    read = ReviewRunRead.model_validate(run)
    read.is_current = True
    return read
