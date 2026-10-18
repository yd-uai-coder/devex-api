import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.catalog import Purpose
from app.ai.llm.content import extract_text_content
from app.core.database import AsyncSessionLocal
from app.core.errors import AppError
from app.detailed_design.function_list import FunctionListModel
from app.detailed_design.plan import PLAN_STAGE, PlanModel
from app.documents.external_design import (
    ExternalDesignModel,
    render_external_design,
    row_counts,
    validate_external_design,
)
from app.documents.external_design import build_model as build_external_design_model
from app.documents.external_design.drafting import (
    ExternalDesignGenerationOutput,
    build_external_design_messages,
)
from app.documents.external_design.drafting import to_draft as to_external_design_draft
from app.documents.implementation_plan import (
    ImplementationPlanModel,
    render_implementation_plan,
    validate_implementation_plan,
)
from app.documents.implementation_plan import build_model as build_implementation_plan_model
from app.documents.implementation_plan import row_counts as implementation_plan_counts
from app.documents.implementation_plan.drafting import (
    ImplementationPlanGenerationOutput,
    build_implementation_plan_messages,
)
from app.documents.implementation_plan.drafting import (
    to_draft as to_implementation_plan_draft,
)
from app.documents.internal_design import (
    InternalDesignModel,
    render_internal_design,
    validate_internal_design,
)
from app.documents.internal_design import build_model as build_internal_design_model
from app.documents.internal_design import row_counts as internal_design_counts
from app.documents.internal_design.drafting import (
    InternalDesignGenerationOutput,
    build_internal_design_messages,
)
from app.documents.internal_design.drafting import to_draft as to_internal_design_draft
from app.documents.issues import DocumentIssue
from app.documents.requirements import (
    RequirementsModel,
    build_model,
    mark_adopted,
    render_requirements,
    source_counts,
    validate_requirements,
)
from app.documents.requirements.drafting import (
    RequirementsGenerationOutput,
    build_requirements_messages,
    to_draft,
)
from app.documents.revision import (
    changed_rows,
    external_design_rows,
    internal_design_rows,
    requirement_rows,
)
from app.documents.test_spec import (
    TestSpecModel,
    design_gap_count,
    detailed_links,
    render_test_spec,
    required_tests,
    simple_links,
    validate_test_spec,
)
from app.documents.test_spec import build_model as build_test_spec_model
from app.documents.test_spec import row_counts as test_spec_counts
from app.documents.test_spec.drafting import TestSpecGenerationOutput, build_test_spec_messages
from app.documents.test_spec.drafting import to_draft as to_test_spec_draft
from app.hearing import as_scale
from app.infrastructure.redis import get_redis_client
from app.models.chat_history import ChatHistory
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import (
    DOC_TYPES_BY_MODE,
    TEST_SPEC,
    GeneratedDocumentRepository,
)
from app.repositories.hearing_point import HearingPointRepository
from app.repositories.project import ProjectRepository
from app.review import (
    DOCUMENTS_GATE,
    build_instruction,
    findings_for,
)
from app.schemas.document import (
    AssumedRequirementRead,
    DocumentIssueRead,
    DocumentSummaryRead,
    ExternalDesignSummaryRead,
    GeneratedDocumentRead,
    RequirementsSummaryRead,
    TestSpecRead,
)
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    DocGenerationInProgressError,
    DocumentNotFoundError,
    GenerationStartInvalidError,
    GenerationUpstreamMissingError,
    RequirementNotAssumedError,
    RequirementNotFoundError,
    TestSpecUpstreamNotReadyError,
)
from app.services.fix_check_service import FixCheckService
from app.services.generation_progress import GenerationProgress, GenerationProgressStore
from app.services.generation_staleness import is_stale
from app.services.llm_resolver import LlmResolver
from app.services.llm_retry import invoke_structured, invoke_with_retry
from app.services.project_deletion import (
    commit_if_live,
    project_vanished,
    purge_deleted_projects,
)
from app.services.review_service import ReviewService
from app.services.test_spec_state import (
    FUNCTION_STAGE,
    TEST_SPEC_STAGES,
    current_test_spec,
    spec_inputs,
)
from app.uml.generation.failures import ReasonCode, classify_failure

logger = structlog.get_logger(__name__)

_DOC_TYPE_LABELS: dict[str, str] = {
    "requirements": "要件定義書",
    "external_design": "外部設計書",
    "internal_design": "内部設計書",
    "implementation_plan": "実装計画書",
    TEST_SPEC: "テスト仕様書",
}

# 各doc_typeの入力として使う、前段で確定済みの文書(doc_type名のタプル)。
# ここに無いdoc_type(requirements)は、代わりにチャット全履歴(transcript)と観点の表を読む。
# 後続の文書は生のチャット履歴を再解釈せず、前段の確定済み文書(model から組み立てた md)だけを
# 入力にすることで、4文書間の一貫性を確保する。照合用の ID(要件ID・API・処理別データフローの ID)
# は、前段の model から写す(app/documents の各 assemble.build_model)。
# 4文書とも構造化出力 → model → md(app/documents の requirements・external_design・
# internal_design・implementation_plan の drafting.py にプロンプトがある)。
_DOC_TYPE_INPUTS: dict[str, tuple[str, ...]] = {
    "external_design": ("requirements",),
    "internal_design": ("requirements", "external_design"),
    "implementation_plan": ("requirements", "internal_design"),
}

_SELF_DIAGNOSIS_SYSTEM_PROMPT = (
    "あなたはレビュアーです。以下は今しがた生成された設計書です。"
    "各ドキュメントの不足・不明瞭な点を「最重要(実装着手に支障)」「中程度(後続の設計判断に影響)」"
    "「軽微(運用上の補足)」の3段階に分類し、日本語の箇条書きで指摘してください。"
    "指摘が無い場合は、その旨を簡潔に述べてください。"
)

# 生成の失敗・中断の理由ごとの文言。チャットに残し、プロジェクトの`generation_failure`にも書く
# (生の例外の文字列は利用者に見せず、ログにだけ残す。理由の分類は classify_failure を共有する)
_FAILURE_MESSAGES: dict[ReasonCode, str] = {
    "QUOTA_EXCEEDED": "本日の利用上限に達しました。時間をおいて再度お試しください。",
    "TOKEN_LIMIT": (
        "AIの入力または出力のトークン数が上限を超えたため、設計書を作れませんでした。"
    ),
    "INVALID_OUTPUT": "AIの出力を設計書として解釈できませんでした。もう一度生成してください。",
    "LLM_UNAVAILABLE": (
        "使うAIのAPIキーが設定されていないため、設計書を作れませんでした。"
        "マイページでモデルかAPIキーを見直してください。"
    ),
    "GENERATION_FAILED": "設計書の生成に失敗しました。時間をおいて再度お試しください。",
    "STALE_GENERATION": (
        "設計書の生成が時間内に終わらなかったため、中断しました。もう一度生成してください。"
    ),
}


def failure_record(reason: ReasonCode, targets: Sequence[str], now: datetime) -> dict:
    """プロジェクトの`generation_failure`に書く失敗の記録(理由・文言・作り直そうとした文書・日時)。"""
    return {
        "reason_code": reason,
        "message": _FAILURE_MESSAGES[reason],
        "targets": list(targets),
        "failed_at": now.isoformat(),
    }


async def generate_documents(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    status_before_generation: str | None = None,
    *,
    start_from: str | None = None,
    finding_ids: Sequence[uuid.UUID] = (),
    llm=None,
) -> None:
    """`BackgroundTasks`から呼び出すエントリポイント。`start_from`は再生成の起点(省略で全部)、
    `finding_ids`は再生成の指示に入れる採用した指摘。

    FastAPI 0.106以降、`yield`を使うDIのセッション(SessionDep)はbackground task実行"前"に
    クローズされるため、background task内でリクエストのセッションをそのまま使うことはできない。
    そのためこの関数はセッションを自前で開始・終了する(公式ドキュメントの推奨パターン:
    "pass identifiers ... retrieve the necessary objects inside the background task itself")。

    project_idに加えuser_idも受け取るのは、所有者チェック無しの専用メソッド(get_by_id_unscoped)を
    リポジトリに増やすのではなく、既存の所有者スコープ版get_by_idへ統一するため。project_id・
    user_idはどちらも値であり、セッションのようにリクエストのライフサイクルに紐づくものではない
    ため、background taskへ渡すこと自体に技術的な制約は無い。

    終わったら、削除の印が付いたプロジェクトの物理削除を試す(生成中は、このジョブが持つロックで
    飛ばされているため。app/services/project_deletion.py)。
    """
    redis = get_redis_client()
    try:
        async with AsyncSessionLocal() as session:
            await DocGeneratorService(session, redis=redis).generate(
                project_id,
                user_id,
                status_before_generation=status_before_generation,
                start_from=start_from,
                finding_ids=finding_ids,
                llm=llm,
            )
    finally:
        await redis.aclose()
        await purge_deleted_projects()


class DocGeneratorService:
    """ヒアリング完了後の4種ドキュメント一括生成+自己診断を担当するサービス。"""

    def __init__(self, session: AsyncSession, *, redis: Redis | None = None) -> None:
        # session: DB操作用の非同期セッション(background task用に独立して開始されたもの)
        # redis: 生成の後の設計レビューの1日の回数を数える
        self._session = session
        self._redis = redis
        self._projects = ProjectRepository(session)
        self._chat_histories = ChatHistoryRepository(session)
        self._documents = GeneratedDocumentRepository(session)
        self._hearing_points = HearingPointRepository(session)
        self._stages = DesignStageService(session)
        self._progress = GenerationProgressStore(redis)

    async def request_generation(
        self,
        project: Project,
        *,
        start_from: str | None = None,
        finding_ids: Sequence[uuid.UUID] = (),
    ) -> str:
        """生成の要求を受け付け、`status`を`generating`にしてcommitする。戻り値は受け付ける前の状態
        (失敗したときに戻す先。バックグラウンドの`generate`へ渡す)。

        `start_from`は再生成の起点(その文書から下流を作り直す。省略で全部)。モードに無い起点は
        `GenerationStartInvalidError`(400)、起点より上流の文書がまだ無ければ
        `GenerationUpstreamMissingError`(409)。詳細設計モードのテスト仕様書は、段階1・段階7 が
        承認済みで古くなければ作れる(違えば`TestSpecUpstreamNotReadyError`(409))。`finding_ids`(再生成の指示に入れる指摘)に
        プロジェクトに無い指摘があれば`ReviewFindingNotFoundError`(404)、採用していない・ヒアリングが
        原因の指摘があれば`SendbackFindingInvalidError`(409)。

        生成中なら`DocGenerationInProgressError`(409)にする。受け付けと同じリクエストの中で
        `generating`にするのは、バックグラウンドで切り替えると、切り替わる前の二度押しを
        すり抜けさせてしまうため。止まった「生成中」は先に回収する(`recover_if_stale`)。"""
        targets = generation_targets(project.mode, start_from)
        for doc_type in DOC_TYPES_BY_MODE[project.mode]:
            if doc_type in targets:
                break
            if await self._documents.get_current(project_id=project.id, doc_type=doc_type) is None:
                raise GenerationUpstreamMissingError(
                    "上流の設計書がまだ無いため、ここから再生成できません。"
                )
        if TEST_SPEC in targets and project.mode == "detailed":
            approved = await self._stages.approved_stages(project, TEST_SPEC_STAGES)
            if len(approved) < len(TEST_SPEC_STAGES):
                raise TestSpecUpstreamNotReadyError(
                    "テスト仕様書は、段階1(処理)と段階7(実装計画)を承認してから作れます。"
                )
        await ReviewService(self._session).sendback_findings(
            project.id, finding_ids, strict=True
        )
        await self.recover_if_stale(project)
        if project.status == "generating":
            raise DocGenerationInProgressError(
                "設計書を生成しています。完了してから再度お試しください。"
            )
        status_before_generation = project.status
        project.status = "generating"
        # 前の失敗の知らせは、次の生成を受け付けた時点で消す
        project.generation_failure = None
        await self._session.commit()
        # updated_at(生成を始めた時刻。回収の判定に使う)はDB側で決まるため読み直す
        await self._session.refresh(project)
        return status_before_generation

    async def recover_if_stale(self, project: Project, *, now: datetime | None = None) -> bool:
        """しきい値(15分)を超えて`generating`のままのプロジェクトを、生成前の状態へ戻す
        (app/services/generation_staleness.py)。戻した場合はTrue。

        生成前の状態は記録していないため、文書が既にあれば`revising`、無ければ`interviewing`にする
        (どちらも再度「生成する」を押せる状態)。中断したことはチャットと`generation_failure`に
        残す(作り直そうとした文書は進み具合から読む。消えていればモードの全部とみなす)。"""
        if project.status != "generating":
            return False
        # updated_at はDB側で更新されるため、失効していれば読み直す(非同期では暗黙に読めない)
        await self._session.refresh(project, ["updated_at"])
        if not is_stale(project.updated_at, now or datetime.now(UTC)):
            return False
        has_documents = bool(await self._documents.list_current_for_project(project.id))
        project.status = "revising" if has_documents else "interviewing"
        progress = await self._progress.read(project.id)
        targets = (
            progress.targets if progress is not None else generation_targets(project.mode, None)
        )
        project.generation_failure = failure_record(
            "STALE_GENERATION", targets, now or datetime.now(UTC)
        )
        await self._chat_histories.add(
            project_id=project.id,
            sender="others",
            message=_FAILURE_MESSAGES["STALE_GENERATION"],
        )
        await self._session.commit()
        await self._session.refresh(project)
        logger.warning("documents_generation_stale", project_id=str(project.id))
        return True

    async def generate(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        status_before_generation: str | None = None,
        start_from: str | None = None,
        finding_ids: Sequence[uuid.UUID] = (),
        llm=None,
        review_llm=None,
        judge_llm=None,
    ) -> None:
        """チャット全履歴をコンテキストに、モードごとの設計書(simpleは4種、detailedは要件定義・
        外部設計の2種。DOC_TYPES_BY_MODE)を生成し、続けて自己診断を行う。

        設計レビューがオン(app/review/policy.py の実効値)のアカウントは、自己診断の代わりに、
        生成を完了にした後で設計レビューを流す(`_review_after_generation`。生成の完了を
        レビューの分だけ待たせない)。`review_llm`はテストでレビューの LLM を差し替える。
        プロジェクトが見つからない場合は何もしない(background task内なので例外を送出しても
        呼び出し元には伝播しないため、静かに終了する)。

        LLM呼び出し失敗時(quota超過等)は`status`を呼び出し前の状態(`interviewing`または
        `revising`)に戻し再試行可能にする ── 失敗時に`generating`のまま固定されると
        ユーザーが再度「生成する」を押せなくなるため。`revising`から始まった再生成が
        失敗した場合に`interviewing`へ巻き戻すと、既に生成済みだったという文脈が失われる
        ため、開始時点の状態を記憶しておいて戻す。
        失敗内容は`sender='others'`のchat_historiesにも記録し、チャット画面に表示できるようにする。
        失敗したときは、途中まで作った版と古い版の削除を rollback で取り消してから状態を戻す
        (4文書の組がそろわない状態を残さないため)。

        `status_before_generation`は`request_generation`が受け付けたときの状態。省略したときは
        (受け付けを経ない呼び出し)ここで`generating`へ切り替える。

        `start_from`(再生成の起点)より上流の文書は作り直さず、表示中の版を下流の入力に使う。
        生成の間は、作り直す文書・できた文書・今作っている文書を Redis に置く
        (app/services/generation_progress.py。終わったら消す)。

        `finding_ids`(設計レビューの採用した指摘)は、作り直す文書ごとに、関連する指摘だけを
        修正指示にして生成のプロンプトに入れる(app/review/sendback.py)。

        テスト仕様書(`TEST_SPEC`)は設計書の後に作る(簡易モードは毎回、詳細設計モードは
        `start_from="test_spec"`のときだけ。`generation_targets`)。設計書と同じトランザクションに
        入れ、自己診断・設計レビュー・変更の影響の印には渡さない。テスト仕様書だけを作ったときは、
        自己診断も設計レビューも流さない。
        """
        project = await self._projects.get_by_id(project_id, user_id=user_id)
        if project is None:
            return

        if status_before_generation is None:
            status_before_generation = project.status
            project.status = "generating"
            await self._session.commit()
        user = await self._session.get(User, user_id)
        reviewing = user is not None and await LlmResolver(self._session).review_effective(user)

        try:
            llm = llm or (
                await LlmResolver(self._session).resolve(user_id, Purpose.GENERATION)
            ).llm
            sendback = await ReviewService(self._session).sendback_findings(
                project_id, finding_ids
            )
            history = await self._chat_histories.list_for_project(project_id)
            transcript = _render_transcript(history)

            generated: dict[str, str] = {}
            requirements: RequirementsModel | None = None
            external: ExternalDesignModel | None = None
            internal: InternalDesignModel | None = None
            plan: ImplementationPlanModel | None = None
            doc_types = DOC_TYPES_BY_MODE[project.mode]
            targets = generation_targets(project.mode, start_from)
            progress = GenerationProgress(targets=list(targets))
            await self._progress.write(project_id, progress)
            # 詳細設計モードの変更の影響は、生成の前に表示していた版と比べる
            previous_models: dict[str, dict | None] = {}
            for doc_type in doc_types:
                current = await self._documents.get_current(
                    project_id=project_id, doc_type=doc_type
                )
                previous_models[doc_type] = current.model if current is not None else None
            new_models: dict[str, dict | None] = {}
            for doc_type in doc_types:
                if doc_type not in targets:
                    # 起点より上流は作り直さず、表示中の版を下流の入力にする
                    kept = await self._documents.get_current(
                        project_id=project_id, doc_type=doc_type
                    )
                    if kept is None:
                        raise GenerationUpstreamMissingError(f"{doc_type} not found")
                    generated[doc_type] = kept.content
                    if doc_type == "requirements":
                        requirements = parse_requirements_model(kept.model)
                    elif doc_type == "external_design":
                        external = parse_external_design_model(kept.model)
                    elif doc_type == "internal_design":
                        internal = parse_internal_design_model(kept.model)
                    else:
                        plan = parse_implementation_plan_model(kept.model)
                    continue
                progress.current = doc_type
                await self._progress.write(project_id, progress)
                model: dict | None = None
                # 再生成を前の版の差分にとどめるため、上流の文書で変わった行を渡す
                changed = upstream_changes(
                    doc_type, previous_models, requirements, external, internal
                )
                instruction = build_instruction(findings_for([doc_type], sendback))
                if doc_type == "requirements":
                    content, requirements = await self._generate_requirements(
                        project, transcript, instruction, llm=llm
                    )
                    model = requirements.model_dump(mode="json")
                elif doc_type == "external_design":
                    content, external = await self._generate_external_design(
                        project,
                        generated["requirements"],
                        requirements,
                        changed,
                        instruction,
                        llm=llm,
                    )
                    model = external.model_dump(mode="json")
                elif doc_type == "internal_design":
                    content, internal = await self._generate_internal_design(
                        project, generated, external, changed, instruction, llm=llm
                    )
                    model = internal.model_dump(mode="json")
                else:
                    content, plan = await self._generate_implementation_plan(
                        project, generated, requirements, internal, changed, instruction, llm=llm
                    )
                    model = plan.model_dump(mode="json")
                await self._documents.create_version(
                    project_id=project_id, doc_type=doc_type, content=content, model=model
                )
                generated[doc_type] = content
                new_models[doc_type] = model
                progress.done.append(doc_type)
            if TEST_SPEC in targets:
                progress.current = TEST_SPEC
                await self._progress.write(project_id, progress)
                content, test_spec = await self._generate_test_spec(
                    project, generated, requirements, external, internal, plan, llm=llm
                )
                await self._documents.create_version(
                    project_id=project_id,
                    doc_type=TEST_SPEC,
                    content=content,
                    model=test_spec.model_dump(mode="json"),
                )
                progress.done.append(TEST_SPEC)
            progress.current = None
            await self._progress.write(project_id, progress)
            designed = [doc_type for doc_type in targets if doc_type in doc_types]
            for doc_type in designed:
                await self._stages.record_document_change(
                    project, doc_type, previous_models[doc_type], new_models[doc_type]
                )

            if designed and not reviewing:
                diagnosis = await self._self_diagnose(generated, llm=llm)
                await self._chat_histories.add(
                    project_id=project_id, sender="others", message=diagnosis
                )


        except Exception as exc:  # noqa: BLE001 -- どの失敗要因も再試行可能な状態に戻す
            await self._session.rollback()
            await self._progress.clear(project_id)
            if await project_vanished(self._session, project_id, job="documents"):
                return  # 生成中に削除された(外部キーの違反も含む)。失敗として記録しない
            logger.warning(
                "documents_generation_failed",
                project_id=str(project_id),
                error_type=type(exc).__name__,
            )
            # rollbackで失効したプロジェクトを読み直してから、状態を戻す
            project = await self._projects.get_by_id(project_id, user_id=user_id)
            if project is None:
                return
            project.status = status_before_generation
            # LLM_QUOTA_EXCEEDEDはdocs/internal_design.md 3.4節が定めるとおり、
            # ユーザーに分かりやすい専用メッセージを表示する。画面は`generation_failure`で知らせる
            reason = classify_failure(exc).reason_code
            project.generation_failure = failure_record(
                reason, _failure_targets(project.mode, start_from), datetime.now(UTC)
            )
            await self._chat_histories.add(
                project_id=project_id, sender="others", message=_FAILURE_MESSAGES[reason]
            )
            await commit_if_live(self._session, project_id, job="documents")
            return

        project.status = "completed"
        # 生成中に削除されていたら、作った版ごと捨てる
        live = await commit_if_live(self._session, project_id, job="documents")
        await self._progress.clear(project_id)
        if not live:
            return
        logger.info(
            "documents_generated",
            project_id=str(project_id),
            doc_count=len(targets),
            start_from=start_from,
        )
        if designed and reviewing and user is not None:
            await self._review_after_generation(project, user, generated, llm, review_llm)
            await self._check_fixes(project_id, user, judge_llm)

    async def _check_fixes(self, project_id: uuid.UUID, user: User, judge_llm) -> None:
        """生成の後に、採用した指摘が直ったかを判定する(シャドーモード。失敗は生成を失敗に
        しない)。レビューで commit した後なので、プロジェクトは読み直す。"""
        try:
            project = await self._session.get(Project, project_id)
            if project is not None:
                await FixCheckService(self._session, redis=self._redis, llm=judge_llm).run_for(
                    project, user
                )
        except Exception as exc:  # noqa: BLE001 -- 判定の失敗は生成を失敗にしない
            await self._session.rollback()
            logger.warning(
                "fix_checks_after_generation_failed",
                project_id=str(project_id),
                error_type=type(exc).__name__,
            )

    async def _review_after_generation(
        self, project: Project, user: User, generated: dict[str, str], llm, review_llm
    ) -> None:
        """生成の後の設計レビュー。受け付けで止まった(上限・キー無し・実行中)ときは失敗の回を
        残し、実行で失敗したときと同じく、生成と同じモデルの自己診断に切り替える。"""
        project_id = project.id
        if await project_vanished(self._session, project_id, job="documents_review"):
            return
        review = ReviewService(self._session, redis=self._redis, llm=review_llm)
        try:
            run = await review.start_review(project, user, gate=DOCUMENTS_GATE, trigger="auto")
        except Exception as exc:  # noqa: BLE001 -- レビューの失敗は生成を失敗にしない
            if not isinstance(exc, AppError):
                # 想定外の失敗(DB など)だけ戻す。戻すと失効するプロジェクトは読み直す
                # (非同期では暗黙に読めない)
                await self._session.rollback()
                if await project_vanished(self._session, project_id, job="documents_review"):
                    return  # レビューの受け付けの間に削除された
                project = await self._session.get(Project, project_id) or project
            await review.record_failed_start(project, trigger="auto", exc=exc)
            await self._fallback_self_diagnose(project_id, generated, llm)
            return
        finished = await review.execute(run.id)
        if finished is None or finished.status != "succeeded":
            await self._fallback_self_diagnose(project_id, generated, llm)

    async def _fallback_self_diagnose(
        self, project_id: uuid.UUID, generated: dict[str, str], llm
    ) -> None:
        """レビューに失敗したときの自己診断。これも失敗したらログだけ残す(文書は完了のまま)。"""
        try:
            diagnosis = await self._self_diagnose(generated, llm=llm)
        except Exception as exc:  # noqa: BLE001 -- 文書の生成は終わっているため、止めない
            logger.warning(
                "self_diagnosis_failed", project_id=str(project_id), error_type=type(exc).__name__
            )
            return
        await self._chat_histories.add(project_id=project_id, sender="others", message=diagnosis)
        await commit_if_live(self._session, project_id, job="self_diagnosis")


    async def list_current_documents(self, project_id: uuid.UUID) -> list[GeneratedDocument]:
        """生成された設計書(各doc_typeの現在表示中のバージョンのみ)一覧を取得する。"""
        return await self._documents.list_current_for_project(project_id)

    async def get_document(self, *, project: Project, doc_id: uuid.UUID) -> GeneratedDocument:
        """指定ドキュメントを1件取得する(他プロジェクトのものは404扱い)。
        GeneratedDocumentRepositoryはProjectRepositoryのような所有権スコープの
        get_by_idを持たないため、その意味づけ(見つからなければNotFound)をここに集約する
        (ルーターがRepositoryを直接参照しないため)。"""
        document = await self._documents.get_by_id(doc_id)
        if document is None or document.project_id != project.id:
            raise DocumentNotFoundError(f"Document {doc_id} not found")
        return document

    async def list_versions(self, project_id: uuid.UUID, doc_type: str) -> list[GeneratedDocument]:
        """指定doc_typeの保管済み全バージョン(最大3件)を新しい順に返す。"""
        return await self._documents.list_versions(project_id=project_id, doc_type=doc_type)

    async def restore_version(
        self, project_id: uuid.UUID, doc_type: str, version: int
    ) -> GeneratedDocument:
        """指定バージョンを表示中(is_current)に切り替える。バージョン番号は増やさず、新しい行も
        作らない(既存版の内容も書き換えない)。指定バージョンが存在しなければDocumentNotFoundError。
        切り替える前に表示していた版と比べて、変更の影響を段階に記録する(model を持つ文書)。"""
        previous = await self._documents.get_current(project_id=project_id, doc_type=doc_type)
        previous_model = previous.model if previous is not None else None
        restored = await self._documents.set_current(
            project_id=project_id, doc_type=doc_type, version=version
        )
        if restored is None:
            raise DocumentNotFoundError(f"{doc_type} version {version} not found")
        project = await self._session.get(Project, project_id)
        if project is not None:
            await self._stages.record_document_change(
                project, doc_type, previous_model, restored.model
            )
        await self._session.commit()
        return restored

    async def adopt_requirement(self, project: Project, req_id: str) -> GeneratedDocument:
        """現在の版の要件定義書の推測の要件を、利用者が確かめた「採用」にする(LLMは呼ばない)。

        同じ版の model と md を書き換え、版は増やさない(中身は変わらないので、後続の段階を
        古くしない)。生成中は409。要件が無い・model の無い版は404、推測でない要件は409。"""
        if project.status == "generating":
            raise DocGenerationInProgressError(
                "設計書を生成しています。完了してから再度お試しください。"
            )
        document = await self._documents.get_current(
            project_id=project.id, doc_type="requirements"
        )
        if document is None:
            raise DocumentNotFoundError("requirements not found")
        model = parse_requirements_model(document.model)
        if model is None or all(row.id != req_id for row in model.rows):
            raise RequirementNotFoundError(f"Requirement {req_id} not found")
        adopted = mark_adopted(model, req_id)
        if adopted is None:
            raise RequirementNotAssumedError(f"Requirement {req_id} is not assumed")
        await self._documents.replace_model(
            document,
            content=render_requirements(adopted),
            model=adopted.model_dump(mode="json"),
        )
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def _generate_requirements(
        self, project: Project, transcript: str, instruction: str = "", *, llm
    ) -> tuple[str, RequirementsModel]:
        """要件定義書を構造化出力で下書きし、model と md を作る。入力は会話・観点の表・前の版の
        要件(ID を引き継ぐため)。出どころは観点の状態を上限に直す(app/documents/requirements)。"""
        states = await self._hearing_points.load_states(project.id)
        scale = as_scale(project.hearing_scale)
        current = await self._documents.get_current(
            project_id=project.id, doc_type="requirements"
        )
        previous = parse_requirements_model(current.model if current else None)
        messages = build_requirements_messages(transcript, states, scale, previous, instruction)
        output = await invoke_structured(llm, RequirementsGenerationOutput, messages)
        model = build_model(to_draft(output), previous, states, scale)
        return render_requirements(model), model

    async def _generate_external_design(
        self,
        project: Project,
        requirements_md: str,
        requirements: RequirementsModel | None,
        changed_upstream: list[str],
        instruction: str = "",
        *,
        llm,
    ) -> tuple[str, ExternalDesignModel]:
        """外部設計書を構造化出力で下書きし、model と md を作る。入力は要件定義書の md、前の版の
        画面・API(画面ID を引き継ぎ、変えない行をそのまま使うため)と、要件定義書の変わった行。
        要件ID の照合用に要件定義書の model を写す(app/documents/external_design)。"""
        current = await self._documents.get_current(
            project_id=project.id, doc_type="external_design"
        )
        previous = parse_external_design_model(current.model if current else None)
        messages = build_external_design_messages(
            requirements_md, previous, changed_upstream, instruction
        )
        output = await invoke_structured(llm, ExternalDesignGenerationOutput, messages)
        model = build_external_design_model(
            to_external_design_draft(output), previous, requirements
        )
        return render_external_design(model), model

    async def _generate_internal_design(
        self,
        project: Project,
        generated: dict[str, str],
        external: ExternalDesignModel | None,
        changed_upstream: list[str],
        instruction: str = "",
        *,
        llm,
    ) -> tuple[str, InternalDesignModel]:
        """内部設計書を構造化出力で下書きし、model と md を作る(app/documents/internal_design)。
        入力は要件定義書・外部設計書の md、前の版の行と上流の変わった行。処理別データフローの ID
        は前の版の model から引き継ぎ、外部設計書の API(検証が照らす)は外部設計書の model から
        写す。"""
        current = await self._documents.get_current(
            project_id=project.id, doc_type="internal_design"
        )
        previous = parse_internal_design_model(current.model if current else None)
        messages = build_internal_design_messages(
            generated["requirements"],
            generated["external_design"],
            previous,
            changed_upstream,
            instruction,
        )
        output = await invoke_structured(llm, InternalDesignGenerationOutput, messages)
        apis = [a.label for a in external.apis] if external else []
        model = build_internal_design_model(to_internal_design_draft(output), previous, apis)
        return render_internal_design(model), model

    async def _generate_implementation_plan(
        self,
        project: Project,
        generated: dict[str, str],
        requirements: RequirementsModel | None,
        internal: InternalDesignModel | None,
        changed_upstream: list[str],
        instruction: str = "",
        *,
        llm,
    ) -> tuple[str, ImplementationPlanModel]:
        """実装計画書を構造化出力で下書きし、model と md を作る(app/documents/implementation_plan)。
        入力は要件定義書・内部設計書の md、前の版のタスクと上流の変わった行。タスクのキーは前の版の
        model から引き継ぎ、処理別データフローの ID と要件ID(検証が照らす)は内部設計書・
        要件定義書の model から写す。"""
        current = await self._documents.get_current(
            project_id=project.id, doc_type="implementation_plan"
        )
        previous = parse_implementation_plan_model(current.model if current else None)
        messages = build_implementation_plan_messages(
            generated["requirements"],
            generated["internal_design"],
            previous,
            changed_upstream,
            instruction,
        )
        output = await invoke_structured(llm, ImplementationPlanGenerationOutput, messages)
        model = build_implementation_plan_model(
            to_implementation_plan_draft(output),
            previous,
            dataflow_ids=[f.id for f in internal.dataflows] if internal else [],
            requirements=requirements,
        )
        return render_implementation_plan(model), model

    async def _generate_test_spec(
        self,
        project: Project,
        generated: Mapping[str, str],
        requirements: RequirementsModel | None,
        external: ExternalDesignModel | None,
        internal: InternalDesignModel | None,
        plan: ImplementationPlanModel | None,
        *,
        llm,
    ) -> tuple[str, TestSpecModel]:
        """テスト仕様書を構造化出力で下書きし、model と md を作る(app/documents/test_spec)。
        入力は要件定義書の md と外部設計書の画面・API の行、コードが決めた必要なテストの一覧。
        処理・単位は、簡易は内部設計書・実装計画書、詳細は段階1・段階7 から引く。生成した時点の
        入力の版を model に写す(「古い」の判定)。"""
        if project.mode == "detailed":
            approved = await self._stages.approved_stages(project, TEST_SPEC_STAGES)
            links = detailed_links(
                _parse(FunctionListModel, approved.get(FUNCTION_STAGE)),
                _parse(PlanModel, approved.get(PLAN_STAGE)),
            )
        else:
            links = simple_links(internal, plan)
        obligations = required_tests(requirements, external or ExternalDesignModel())
        messages = build_test_spec_messages(generated["requirements"], external, obligations)
        output = await invoke_structured(llm, TestSpecGenerationOutput, messages)
        model = build_test_spec_model(
            to_test_spec_draft(output),
            requirements=requirements,
            external=external,
            links=links,
            inputs=await spec_inputs(self._session, project),
        )
        return render_test_spec(model), model

    async def get_test_spec(self, project: Project) -> TestSpecRead:
        """テスト仕様書の今の版と、件数・検証の指摘・古いか。まだ無ければ`DocumentNotFoundError`。
        model の無い・壊れた版は件数0・指摘なし・古くない、とする。"""
        current = await current_test_spec(self._session, project)
        if current is None:
            raise DocumentNotFoundError("test_spec not found")
        return TestSpecRead(
            document=document_read(current.document),
            counts=test_spec_counts(current.model),
            issues=_issues_read(validate_test_spec(current.model)),
            outdated=current.outdated,
            design_gaps=design_gap_count(current.model),
        )

    async def _self_diagnose(self, generated: dict[str, str], *, llm) -> str:
        """生成済みの文書(モードにより4種または2種)をまとめて入力し、不足・不明瞭な点を
        3段階で自己診断させる。"""
        combined = "\n\n".join(
            f"## {_DOC_TYPE_LABELS[doc_type]}\n{content}" for doc_type, content in generated.items()
        )
        messages = [
            SystemMessage(content=_SELF_DIAGNOSIS_SYSTEM_PROMPT),
            HumanMessage(content=combined),
        ]

        async def _call() -> str:
            response = await llm.ainvoke(messages)
            return extract_text_content(response.content)

        return await invoke_with_retry(_call, messages=messages)


def generation_targets(mode: str, start_from: str | None) -> tuple[str, ...]:
    """再生成で作り直す文書(起点から下流。上流 → 下流の順)。起点が無ければモードの全部。
    下流は上流を入力にして作るので、起点を作り直したら下流も必ず作り直す(整合を保つため)。
    モードに無い起点は`GenerationStartInvalidError`(400)。

    テスト仕様書は最下流。簡易モードは設計書に続けて必ず作り直し、詳細設計モードは段階7の
    承認の後に`start_from="test_spec"`でだけ作る(設計書の再生成では作らない)。"""
    doc_types = DOC_TYPES_BY_MODE[mode]
    if start_from == TEST_SPEC:
        return (TEST_SPEC,)
    if start_from is not None and start_from not in doc_types:
        raise GenerationStartInvalidError("このモードには無い設計書から再生成しようとしました。")
    targets = doc_types if start_from is None else doc_types[doc_types.index(start_from) :]
    return (*targets, TEST_SPEC) if mode == "simple" else targets


def _failure_targets(mode: str, start_from: str | None) -> tuple[str, ...]:
    """失敗の記録に書く「作り直そうとした文書」。起点がモードに無ければ(その失敗自体)モードの全部。"""
    try:
        return generation_targets(mode, start_from)
    except GenerationStartInvalidError:
        return generation_targets(mode, None)


def _render_transcript(history: list[ChatHistory]) -> str:
    """chat_historiesを、ドキュメント生成プロンプトに埋め込むための読みやすいテキストに変換する。
    sender='others'(過去の自己診断結果)は生成の入力には含めない。"""
    _SPEAKER_LABELS = {
        "user": "ユーザー",
        "ai": "AI",
        "intake": "初期入力",
        "attachment": "添付資料",
    }
    lines = [
        f"[{_SPEAKER_LABELS.get(entry.sender, entry.sender)}] {entry.message}"
        for entry in history
        if entry.sender != "others"
    ]
    return "\n".join(lines)


def upstream_changes(
    doc_type: str,
    previous_models: Mapping[str, dict | None],
    requirements: RequirementsModel | None,
    external: ExternalDesignModel | None,
    internal: InternalDesignModel | None,
) -> list[str]:
    """文書の上流(_DOC_TYPE_INPUTS)で、前の版から今回の生成で変わった行(後続の再生成を前の版の
    差分にとどめる手がかり。app/documents/revision.py)。上流の前の版が無ければ空。"""
    rows = {
        "requirements": (
            requirement_rows(parse_requirements_model(previous_models.get("requirements"))),
            requirement_rows(requirements),
        ),
        "external_design": (
            external_design_rows(
                parse_external_design_model(previous_models.get("external_design"))
            ),
            external_design_rows(external),
        ),
        "internal_design": (
            internal_design_rows(
                parse_internal_design_model(previous_models.get("internal_design"))
            ),
            internal_design_rows(internal),
        ),
    }
    return [
        change
        for upstream in _DOC_TYPE_INPUTS.get(doc_type, ())
        for change in changed_rows(*rows[upstream])
    ]


def _parse[M: BaseModel](model_type: type[M], raw: Mapping | tuple | None) -> M | None:
    """保存した model を読む。無い・壊れていれば None。段階は(承認した版, 内容)の組も受ける。"""
    if isinstance(raw, tuple):
        raw = raw[1]
    if raw is None:
        return None
    try:
        return model_type.model_validate(raw)
    except ValidationError:
        return None


def parse_requirements_model(raw: dict | None) -> RequirementsModel | None:
    """保存した model を読む。無い・壊れていれば None(md のまま使う)。"""
    if raw is None:
        return None
    try:
        return RequirementsModel.model_validate(raw)
    except ValidationError:
        return None


def requirements_summary(document: GeneratedDocument) -> RequirementsSummaryRead | None:
    """プレビューに出す要件定義書の出どころの件数・推測の要件・検証の指摘(LLMは呼ばない)。
    model の無い版・ほかの文書は None。"""
    if document.doc_type != "requirements":
        return None
    model = parse_requirements_model(document.model)
    if model is None:
        return None
    return RequirementsSummaryRead(
        counts={**source_counts(model)},
        assumed=[
            AssumedRequirementRead(id=r.id, kind=r.kind, text=r.text)
            for r in model.rows
            if r.source == "assumed"
        ],
        issues=_issues_read(validate_requirements(model)),
    )


def parse_external_design_model(raw: dict | None) -> ExternalDesignModel | None:
    """保存した外部設計書の model を読む。無い・壊れていれば None(md のまま使う)。"""
    if raw is None:
        return None
    try:
        return ExternalDesignModel.model_validate(raw)
    except ValidationError:
        return None


def external_design_summary(document: GeneratedDocument) -> ExternalDesignSummaryRead | None:
    """プレビューに出す外部設計書の行数と検証の指摘(LLMは呼ばない)。model の無い版・ほかの
    文書は None。"""
    if document.doc_type != "external_design":
        return None
    model = parse_external_design_model(document.model)
    if model is None:
        return None
    return ExternalDesignSummaryRead(
        counts=row_counts(model), issues=_issues_read(validate_external_design(model))
    )


def parse_internal_design_model(raw: dict | None) -> InternalDesignModel | None:
    """保存した内部設計書の model を読む。無い・壊れていれば None(md のまま使う)。"""
    if raw is None:
        return None
    try:
        return InternalDesignModel.model_validate(raw)
    except ValidationError:
        return None


def parse_implementation_plan_model(raw: dict | None) -> ImplementationPlanModel | None:
    """保存した実装計画書の model を読む。無い・壊れていれば None(md のまま使う)。"""
    if raw is None:
        return None
    try:
        return ImplementationPlanModel.model_validate(raw)
    except ValidationError:
        return None


def internal_design_summary(document: GeneratedDocument) -> DocumentSummaryRead | None:
    """プレビューに出す内部設計書の行数と検証の指摘(LLMは呼ばない)。model の無い版・ほかの
    文書は None。"""
    if document.doc_type != "internal_design":
        return None
    model = parse_internal_design_model(document.model)
    if model is None:
        return None
    return DocumentSummaryRead(
        counts=internal_design_counts(model),
        issues=_issues_read(validate_internal_design(model)),
    )


def implementation_plan_summary(document: GeneratedDocument) -> DocumentSummaryRead | None:
    """プレビューに出す実装計画書の行数と検証の指摘(LLMは呼ばない)。model の無い版・ほかの
    文書は None。"""
    if document.doc_type != "implementation_plan":
        return None
    model = parse_implementation_plan_model(document.model)
    if model is None:
        return None
    return DocumentSummaryRead(
        counts=implementation_plan_counts(model),
        issues=_issues_read(validate_implementation_plan(model)),
    )


def _issues_read(issues: list[DocumentIssue]) -> list[DocumentIssueRead]:
    return [
        DocumentIssueRead(level=i.level, code=i.code, message=i.message, targets=list(i.targets))
        for i in issues
    ]


def document_read(document: GeneratedDocument) -> GeneratedDocumentRead:
    """文書の応答。要件定義書は出どころの件数・推測の要件・検証の指摘を、外部設計書・内部設計書・
    実装計画書は行数と検証の指摘を付ける。"""
    read = GeneratedDocumentRead.model_validate(document)
    read.requirements = requirements_summary(document)
    read.external_design = external_design_summary(document)
    read.internal_design = internal_design_summary(document)
    read.implementation_plan = implementation_plan_summary(document)
    return read
