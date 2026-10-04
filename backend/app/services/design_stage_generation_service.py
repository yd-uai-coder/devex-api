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
- 生成できる段階は`STAGE_GENERATORS`に登録したものだけ(Phase 16 は段階1、Phase 17 で段階2)。
- 段階2は、段階の内容のほかに機能グループの DFD(`uml_diagrams`)とデータ項目(`data_items`)も
  書く。段階の保存と同じトランザクションで書き、失敗したらまとめて取り消す。そのため生成の関数には
  `StageGenerationContext`でセッションとプロジェクトを渡す(Phase 17)。
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from langchain_core.messages import BaseMessage
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.gemini import get_gemini_llm
from app.core.database import AsyncSessionLocal
from app.detailed_design.data_flow import (
    MAX_DFD_GROUPS,
    DataFlowModel,
    dfd_subject,
    group_functions,
    merge_summaries,
)
from app.detailed_design.data_flow_drafting import (
    GroupDfdGenerationOutput,
    ProcessSummaryGenerationOutput,
    build_group_dfd_messages,
    build_summary_messages,
    to_dfd_output,
    to_summary_drafts,
)
from app.detailed_design.drafting import (
    FunctionListGenerationOutput,
    build_function_list_messages,
    to_drafts,
)
from app.detailed_design.function_list import FunctionListModel, merge_draft
from app.detailed_design.stages import Fingerprint
from app.detailed_design.validation import StageSources
from app.models.project import Project
from app.repositories.design_stage import DesignStageRepository
from app.repositories.project import ProjectRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.schemas.design_stage import DesignStageRead
from app.services.data_item_service import DataItemService
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    DesignStageGenerationInProgressError,
    DesignStageGenerationNotSupportedError,
    DesignStageInvalidError,
    DesignStageLockedError,
)
from app.services.generation_staleness import is_stale
from app.services.llm_retry import invoke_with_retry
from app.uml.domain import NOTATION_TO_VIEW, DfdSemanticModel
from app.uml.generation.failures import ReasonCode, classify_failure, unwrap_structured_result
from app.uml.generation.mapper import required_data_items, to_dfd
from app.uml.generation.prompts import ExistingDataItem

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

@dataclass(frozen=True)
class StageGenerationContext:
    """下書きの生成の関数に渡す入力。

    - `sources`: 入力の文書の本文と、入力の段階の内容(承認済み)。
    - `previous`: その段階の今の内容(作り直しのとき。初回は None)。
    - `fingerprint`: 生成した時点の入力の版(段階2は DFD の`source_doc_versions`にも記録する)。
    - `session`・`project_id`: 段階のほかに DB へ書く生成(段階2の DFD・データ項目)が使う。
      commitは呼び出し元(execute)が段階の保存と一緒に1回だけ行う。
    """

    llm: Any
    sources: StageSources
    previous: Mapping[str, Any] | None
    fingerprint: Fingerprint
    session: AsyncSession
    project_id: uuid.UUID


StageGenerator = Callable[[StageGenerationContext], Awaitable[dict]]


async def _invoke_structured[T: BaseModel](
    llm, schema: type[T], messages: list[BaseMessage]
) -> T:
    """構造化出力を呼ぶ(解釈の失敗・クォータ超過は invoke_with_retry の規則で再試行する)。"""
    structured_llm = llm.with_structured_output(schema, include_raw=True)

    async def _call() -> T:
        result = await structured_llm.ainvoke(messages)
        return unwrap_structured_result(result, schema)

    return await invoke_with_retry(_call, messages=messages)


async def generate_function_list(context: StageGenerationContext) -> dict:
    """段階1: 外部設計書から処理を下書きし、前の版と突き合わせて処理IDと機能グループを決める。"""
    messages = build_function_list_messages(context.sources.documents.get("external_design", ""))
    output = await _invoke_structured(context.llm, FunctionListGenerationOutput, messages)
    previous = context.previous
    previous_model = FunctionListModel.model_validate(previous) if previous else None
    return merge_draft(to_drafts(output), previous_model).model_dump(mode="json")


async def generate_data_flow(context: StageGenerationContext) -> dict:
    """段階2: 全処理の処理概要表を下書きし、人が選んだ機能グループごとに DFD を下書きする。

    DFD を描くグループ(`dfd_groups`)は人の選択なので、前の版から引き継ぐ。グループの DFD は
    `uml_diagrams`の同じ行(subject=機能グループ名)を上書きし、承認はやり直しになる。"""
    function_list = FunctionListModel.model_validate(context.sources.stages.get(1) or {})
    previous = DataFlowModel.model_validate(context.previous) if context.previous else None
    requirements = context.sources.documents.get("requirements", "")

    summary = await _invoke_structured(
        context.llm,
        ProcessSummaryGenerationOutput,
        build_summary_messages(function_list.functions, requirements),
    )
    model = merge_summaries(to_summary_drafts(summary), function_list, previous)

    data_items = DataItemService(context.session)
    for group in model.dfd_groups:
        functions = group_functions(function_list, group)
        existing = [
            ExistingDataItem(name=item.name, field_names=[f["name"] for f in item.fields])
            for item in await data_items.list_for_project(context.project_id)
        ]
        output = await _invoke_structured(
            context.llm,
            GroupDfdGenerationOutput,
            build_group_dfd_messages(group, functions, model.summaries, requirements, existing),
        )
        converted = to_dfd_output(output, functions)
        ids_by_name = await data_items.resolve_by_name(
            context.project_id, required_data_items(converted)
        )
        await _save_group_dfd(context, group, to_dfd(converted, ids_by_name))
    return model.model_dump(mode="json")


async def _save_group_dfd(
    context: StageGenerationContext, group: str, model: DfdSemanticModel
) -> None:
    """機能グループの DFD を、同じ subject の行に上書きする(無ければ作る)。commitしない。
    配置は消して、画面で最初に開いたときに自動レイアウトさせる(UML図の再生成と同じ)。"""
    diagrams = UmlDiagramRepository(context.session)
    subject = dfd_subject(group)
    diagram = await diagrams.get_by_subject(
        project_id=context.project_id, notation="dfd", subject=subject
    )
    if diagram is None:
        diagram = await diagrams.create(
            project_id=context.project_id,
            view=NOTATION_TO_VIEW["dfd"],
            notation="dfd",
            semantic_model={},
            subject=subject,
        )
    diagram.semantic_model = model.model_dump(mode="json")
    diagram.layout_model = None
    diagram.status = "draft"
    diagram.version += 1
    diagram.source_doc_versions = dict(context.fingerprint)
    diagram.generation_status = "completed"
    diagram.generation_error = None


# 段階番号 → 下書きの生成。登録の無い段階は生成できない(各段階の Phase で足す)。
STAGE_GENERATORS: dict[int, StageGenerator] = {
    1: generate_function_list,
    2: generate_data_flow,
}


def _has_draft(stage: int, model: Mapping[str, Any] | None) -> bool:
    """作り直し(`regenerated`)か初回(`draft`)かの判定に使う、「下書きの内容がある」か。
    段階2は、人がグループの選択だけを保存してから初めて生成するので、処理概要表の行で判定する。"""
    if not model:
        return False
    if stage == 2:
        return bool(model.get("summaries"))
    return True


def _check_request(stage: int, model: Mapping[str, Any] | None) -> None:
    """段階ごとの、生成を受け付ける前の確認。段階2は DFD を描くグループの数(上限を超えたまま
    生成すると、15分の回収のしきい値を超えるおそれがあるため)。"""
    if stage == 2 and model:
        groups = set(DataFlowModel.model_validate(model).dfd_groups)
        if len(groups) > MAX_DFD_GROUPS:
            raise DesignStageInvalidError(
                f"DFD を描く機能グループは {MAX_DFD_GROUPS} つまでです(今は {len(groups)} つ)。"
            )


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
        その段階を生成中 / 段階ごとの確認(段階2の DFD を描くグループの数)。"""
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
        _check_request(stage, row.model if row is not None else None)
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
        regenerating = _has_draft(stage, row.model)
        fingerprint = await self._stages.current_fingerprint(project, stage)
        context = StageGenerationContext(
            llm=llm or get_gemini_llm(),
            sources=sources,
            previous=row.model,
            fingerprint=fingerprint,
            session=self._session,
            project_id=project_id,
        )
        try:
            model = await generator(context)
        except Exception as exc:
            # 途中の変更(段階2の DFD・データ項目も)を残さず、読み直した行に失敗だけを記録する
            # (rollbackで行は期限切れになる)
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
