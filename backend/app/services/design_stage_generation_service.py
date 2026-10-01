"""詳細設計モードの段階のAIの下書きの生成(docs/external_design.md 2.7節「各段階の共通サイクル」)。

受け付け(`request_generation`、リクエスト内)と実行(`execute`、バックグラウンド)を分ける。
UML図の生成(app/services/uml_generation_service.py)と同じ形で、生成の経過は段階の行の
`generation_status`・`generation_error`に残し、画面は段階の一覧をポーリングして完了を待つ。

- 生成中は、同じ段階の生成・保存・承認を409で断る(AIの結果で人の編集を上書きしないため)。
- 下書きは`status='draft'`(初回)・`'regenerated'`(内容のある段階の作り直し)で保存し、
  `version`を1つ増やす(承認済みの段階を作り直すと承認はやり直しになる。UML図の再生成と同じ)。
  生成した時点の入力の版を`input_fingerprint`に記録する。前の承認の記録が残ると、作り直した
  直後でも「古い」と判定されるため(Phase 16 の修正)。
- 15分を超えて生成中のまま止まった段階は、受け付け時と一覧の取得時に失敗へ戻す
  (app/services/generation_staleness.py)。
- 生成できる段階は`STAGE_GENERATORS`に登録したものだけ(Phase 16 は段階1)。
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.gemini import get_gemini_llm
from app.core.database import AsyncSessionLocal
from app.detailed_design.drafting import (
    FunctionListGenerationOutput,
    build_function_list_messages,
    to_drafts,
)
from app.detailed_design.function_list import FunctionListModel, merge_draft
from app.detailed_design.validation import StageSources
from app.models.project import Project
from app.repositories.design_stage import DesignStageRepository
from app.repositories.project import ProjectRepository
from app.schemas.design_stage import DesignStageRead
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    DesignStageGenerationInProgressError,
    DesignStageGenerationNotSupportedError,
    DesignStageLockedError,
)
from app.services.generation_staleness import is_stale
from app.services.llm_retry import invoke_with_retry
from app.uml.generation.failures import ReasonCode, classify_failure, unwrap_structured_result

logger = structlog.get_logger(__name__)

# 段階の下書きが止まった理由のユーザー向けの文言(UML図の文言は「設計図」「ER図」を前提にしている
# ため、段階用に言い換える。理由の分類自体は classify_failure を共有する)
_MESSAGES: dict[ReasonCode, str] = {
    "QUOTA_EXCEEDED": (
        "AIの利用上限に達したため、下書きを作れませんでした。"
        "時間をおいて、もう一度生成してください。"
    ),
    "TOKEN_LIMIT": (
        "AIの入力または出力のトークン数が上限を超えたため、下書きを作れませんでした。"
    ),
    "INVALID_OUTPUT": "AIの出力を下書きとして解釈できませんでした。もう一度生成してください。",
    "GENERATION_FAILED": "下書きの生成に失敗しました。時間をおいて、もう一度生成してください。",
    "STALE_GENERATION": (
        "下書きの生成が時間内に終わらなかったため、中断しました。もう一度生成してください。"
    ),
}

StageGenerator = Callable[[Any, StageSources, Mapping[str, Any] | None], Awaitable[dict]]


async def generate_function_list(
    llm, sources: StageSources, previous: Mapping[str, Any] | None
) -> dict:
    """段階1: 外部設計書から処理を下書きし、前の版と突き合わせて処理IDと機能グループを決める。"""
    messages = build_function_list_messages(sources.documents.get("external_design", ""))
    structured_llm = llm.with_structured_output(FunctionListGenerationOutput, include_raw=True)

    async def _call() -> FunctionListGenerationOutput:
        result = await structured_llm.ainvoke(messages)
        return unwrap_structured_result(result, FunctionListGenerationOutput)

    output = await invoke_with_retry(_call, messages=messages)
    previous_model = FunctionListModel.model_validate(previous) if previous else None
    return merge_draft(to_drafts(output), previous_model).model_dump(mode="json")


# 段階番号 → 下書きの生成。登録の無い段階は生成できない(各段階の Phase で足す)。
STAGE_GENERATORS: dict[int, StageGenerator] = {
    1: generate_function_list,
}


async def run_design_stage_generation(
    project_id: uuid.UUID, user_id: uuid.UUID, stage: int, *, llm=None
) -> None:
    """`BackgroundTasks`から呼び出すエントリポイント。リクエストのセッションはbackground task
    実行前にクローズされるため、セッションを自前で開始・終了する(run_uml_generationと同じ形)。"""
    async with AsyncSessionLocal() as session:
        await DesignStageGenerationService(session).execute(
            project_id=project_id, user_id=user_id, stage=stage, llm=llm
        )


class DesignStageGenerationService:
    """段階のAIの下書きの生成を担当するサービス。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._stages = DesignStageService(session)
        self._rows = DesignStageRepository(session)
        self._projects = ProjectRepository(session)

    async def request_generation(self, project: Project, *, stage: int) -> DesignStageRead:
        """生成を受け付け、段階を「生成中」にする。生成自体は呼び出し元がバックグラウンドで
        `execute`する。未着手の段階は、ここで行を作る(内容は空、`draft`)。

        断る条件(この順): 詳細設計モードでない / 生成に対応していない段階 / 段階が開いていない /
        その段階を生成中。"""
        await self.recover_stale(project.id)
        row, view, _ = await self._stages.stage_view(project, stage)
        if stage not in STAGE_GENERATORS:
            raise DesignStageGenerationNotSupportedError(
                f"Stage {stage} does not support AI drafts yet"
            )
        if not view.is_open:
            raise DesignStageLockedError(
                f"Stage {stage} is locked: missing {', '.join(view.missing_inputs)}"
            )
        if row is not None and row.generation_status == "generating":
            raise DesignStageGenerationInProgressError(
                "この段階の下書きを生成中です。完了してから再度お試しください。"
            )
        if row is None:
            row = await self._rows.create(project_id=project.id, stage=stage, model=None)
        row.generation_status = "generating"
        row.generation_error = None
        row.generation_started_at = datetime.now(UTC)
        await self._session.commit()
        logger.info("design_stage_generation_requested", project_id=str(project.id), stage=stage)
        return await self._stages.read(project.id, stage)

    async def execute(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, stage: int, llm=None
    ) -> None:
        """下書きを生成して段階に保存する。失敗したら理由を残して`failed`にする。
        プロジェクトや「生成中」の段階が見つからなければ何もしない(回収された後など)。"""
        project = await self._projects.get_by_id(project_id, user_id=user_id)
        if project is None:
            return
        row, _, sources = await self._stages.stage_view(project, stage)
        if row is None or row.generation_status != "generating":
            return
        generator = STAGE_GENERATORS[stage]
        regenerating = bool(row.model)
        fingerprint = await self._stages.current_fingerprint(project, stage)
        try:
            model = await generator(llm or get_gemini_llm(), sources, row.model)
        except Exception as exc:
            # 途中の変更を残さず、読み直した行に失敗だけを記録する(rollbackで行は期限切れになる)
            await self._session.rollback()
            row = await self._rows.get(project_id=project_id, stage=stage)
            if row is None:
                return
            failure = classify_failure(exc)
            logger.warning(
                "design_stage_generation_failed",
                project_id=str(project_id),
                stage=stage,
                reason_code=failure.reason_code,
                error=str(exc),
            )
            row.generation_status = "failed"
            row.generation_error = _MESSAGES[failure.reason_code]
            await self._session.commit()
            return

        row.model = model
        row.status = "regenerated" if regenerating else "draft"
        row.input_fingerprint = fingerprint
        row.version += 1
        row.generation_status = "completed"
        row.generation_error = None
        await self._session.commit()
        logger.info("design_stage_generation_completed", project_id=str(project_id), stage=stage)

    async def recover_stale(self, project_id: uuid.UUID, *, now: datetime | None = None) -> int:
        """しきい値(15分)を超えて生成中のまま止まった段階を`failed`にする。回収した数を返す。
        止まった段階が残ると、その段階の生成・保存・承認が409で塞がり続けるため。"""
        now = now or datetime.now(UTC)
        stale = [
            row
            for row in await self._rows.list_for_project(project_id)
            if row.generation_status == "generating"
            and (row.generation_started_at is None or is_stale(row.generation_started_at, now))
        ]
        for row in stale:
            row.generation_status = "failed"
            row.generation_error = _MESSAGES["STALE_GENERATION"]
        if stale:
            await self._session.commit()
            logger.warning(
                "design_stage_generation_recovered", project_id=str(project_id), count=len(stale)
            )
        return len(stale)
