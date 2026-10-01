import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.gemini import get_gemini_llm
from app.core.database import AsyncSessionLocal
from app.models.uml_generation_run import UmlGenerationRun
from app.repositories.data_item import DataItemRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.repositories.uml_generation_run import UmlGenerationRunRepository
from app.services.errors import (
    ErScopeRequiredError,
    TooManySubjectsError,
    UmlGenerationInProgressError,
    UmlSourceDocumentMissingError,
    UmlSubjectNotFoundError,
)
from app.services.generation_staleness import is_stale
from app.services.llm_retry import invoke_with_retry
from app.uml.domain import NOTATION_TO_VIEW, NotationType, empty_semantic_model
from app.uml.generation import (
    GENERATION_SCHEMAS,
    SKIPPED_MESSAGE,
    STALE_MESSAGE,
    DfdGenerationOutput,
    DfdSubject,
    ExistingDataItem,
    GenerationOutput,
    build_generation_messages,
    classify_failure,
    extract_dfd_subjects,
    extract_er_tables,
    required_data_items,
    to_dfd,
    to_semantic_model,
    unwrap_structured_result,
)
from app.uml.validation.structural import MAX_ELEMENTS

logger = structlog.get_logger(__name__)

# 1回の生成リクエスト(チェックボックスでの一括選択)で受け付ける対象の上限。図の「数」の上限では
# なく、1つのバックグラウンドタスクが順番に処理する件数の上限(長時間の占有を避ける)。
MAX_SUBJECTS_PER_REQUEST = 5

# 生成元の文書種別(docs/internal_design.md 3.3節③、D5: component/ER/DFDはすべて内部設計書に対応)
SOURCE_DOC_TYPE = "internal_design"


@dataclass(frozen=True)
class SubjectRequest:
    """生成対象1件の指定。`tables`はER部分図のテーブル選択(再生成で省略すると前回の選択を使う)。"""

    subject: str
    tables: list[str] | None = None


@dataclass(frozen=True)
class GenerationCandidates:
    """内部設計書から決定的に列挙した生成対象の候補。"""

    internal_design_version: int | None
    dfd_subjects: list[DfdSubject]
    er_tables: list[str]


async def run_uml_generation(project_id: uuid.UUID, run_id: uuid.UUID, *, llm=None) -> None:
    """`BackgroundTasks`から呼び出すエントリポイント。リクエストのセッションはbackground task
    実行前にクローズされるため、セッションを自前で開始・終了する
    (app/services/doc_generator_service.pyのgenerate_documentsと同じ理由・同じ形)。"""
    async with AsyncSessionLocal() as session:
        await UmlGenerationService(session).execute_run(
            project_id=project_id, run_id=run_id, llm=llm
        )


class UmlGenerationService:
    """UML図のAI生成(M1)を担当するサービス。受け付け(`request_generation`、リクエスト内)と
    実行(`execute_run`、バックグラウンド)を分け、実行の経過は生成履歴(uml_generation_runs)と
    図の`generation_status`に残す。"""

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._session = session
        self._diagrams = UmlDiagramRepository(session)
        self._runs = UmlGenerationRunRepository(session)
        self._data_items = DataItemRepository(session)
        self._documents = GeneratedDocumentRepository(session)

    async def list_candidates(self, project_id: uuid.UUID) -> GenerationCandidates:
        """DFDの対象(処理)とERのテーブルの候補を、内部設計書の見出しから列挙する(LLMは呼ばない)。
        内部設計書がまだ無ければ候補は空になる。"""
        document = await self._documents.get_current(
            project_id=project_id, doc_type=SOURCE_DOC_TYPE
        )
        if document is None:
            return GenerationCandidates(internal_design_version=None, dfd_subjects=[], er_tables=[])
        return GenerationCandidates(
            internal_design_version=document.version,
            dfd_subjects=extract_dfd_subjects(document.content),
            er_tables=extract_er_tables(document.content),
        )

    async def list_runs(self, project_id: uuid.UUID) -> list[UmlGenerationRun]:
        """生成履歴を新しい順に取得する。"""
        return await self._runs.list_recent(project_id)

    async def request_generation(
        self,
        *,
        project_id: uuid.UUID,
        notation: NotationType,
        subjects: Sequence[SubjectRequest],
    ) -> UmlGenerationRun:
        """生成リクエストを検証して受け付ける。対象の図を`generating`にし(無ければ空の図を作り、
        あれば上書き対象として再利用する)、生成履歴を'running'で作成してcommitする。
        実際のLLM呼び出しはバックグラウンドの`execute_run`が行う。"""
        requests = _normalize_subjects(notation, subjects)
        if len(requests) > MAX_SUBJECTS_PER_REQUEST:
            raise TooManySubjectsError(
                f"一度に生成できる対象は{MAX_SUBJECTS_PER_REQUEST}件までです: {len(requests)}件"
            )

        document = await self._documents.get_current(
            project_id=project_id, doc_type=SOURCE_DOC_TYPE
        )
        if document is None:
            raise UmlSourceDocumentMissingError(
                "内部設計書がまだ生成されていません。先に設計書を生成してください。"
            )
        await self.recover_stale(project_id)
        if await self._diagrams.has_generating(project_id):
            raise UmlGenerationInProgressError(
                "このプロジェクトでは設計図の生成が実行中です。完了してから再度お試しください。"
            )

        scopes = await self._resolve_scopes(project_id, notation, requests, document.content)

        requested: list[dict] = []
        for request in requests:
            diagram = await self._diagrams.get_by_subject(
                project_id=project_id, notation=notation, subject=request.subject
            )
            if diagram is None:
                diagram = await self._diagrams.create(
                    project_id=project_id,
                    view=NOTATION_TO_VIEW[notation],
                    notation=notation,
                    semantic_model=empty_semantic_model(notation).model_dump(mode="json"),
                    subject=request.subject,
                    scope=scopes[request.subject],
                    generation_status="generating",
                )
            else:
                diagram.generation_status = "generating"
                diagram.generation_error = None
                diagram.scope = scopes[request.subject]
            requested.append({"subject": request.subject, "diagram_id": str(diagram.id)})

        run = await self._runs.create(project_id=project_id, notation=notation, requested=requested)
        await self._session.commit()
        return run

    async def recover_stale(self, project_id: uuid.UUID, *, now: datetime | None = None) -> int:
        """しきい値(15分)を超えて生成中のまま止まった図を`failed`(STALE_GENERATION)にし、
        実行中のまま止まった生成履歴を`failed`にする(app/services/generation_staleness.py)。
        回収した図の数を返す。

        生成の受け付け時と図の一覧の取得時に呼ぶ。止まった図が残ると`has_generating`が真のままに
        なり、以後の生成・編集がすべて409で塞がるため。"""
        now = now or datetime.now(UTC)
        generating = await self._diagrams.list_generating(project_id)
        stale_diagrams = [d for d in generating if is_stale(d.updated_at, now)]
        stale_runs = [
            r for r in await self._runs.list_running(project_id) if is_stale(r.started_at, now)
        ]
        if not stale_diagrams and not stale_runs:
            return 0
        for diagram in stale_diagrams:
            diagram.generation_status = "failed"
            diagram.generation_error = STALE_MESSAGE
        for run in stale_runs:
            finished = {r["diagram_id"] for r in run.results}
            # 結果が記録されていない対象を、中断として記録する(JSON列は新しいリストを代入する)
            run.results = [dict(r) for r in run.results] + [
                {
                    "subject": entry["subject"],
                    "diagram_id": entry["diagram_id"],
                    "outcome": "failed",
                    "reason_code": "STALE_GENERATION",
                    "message": STALE_MESSAGE,
                }
                for entry in run.requested
                if entry["diagram_id"] not in finished
            ]
            run.status = "failed"
            run.finished_at = now
        await self._session.commit()
        logger.warning(
            "uml_generation_stale",
            project_id=str(project_id),
            diagrams=len(stale_diagrams),
            runs=len(stale_runs),
        )
        return len(stale_diagrams)

    async def execute_run(self, *, project_id: uuid.UUID, run_id: uuid.UUID, llm=None) -> None:
        """受け付け済みの生成リクエストを実行する。対象を順番に1件ずつ生成し(1件=構造化出力1回)、
        1件ごとにcommitする。クォータ超過で止まった場合、残りの対象はLLMを呼ばずに`skipped`
        にする(同じ日のうちに呼んでも失敗するだけで、クォータ回復後に再指示してもらう)。
        失敗した対象の図は、前回の生成結果(あれば)を残したまま`failed`にする。"""
        run = await self._runs.get_by_id(run_id, project_id=project_id)
        if run is None:
            return
        llm = llm or get_gemini_llm()
        # rollback(失敗時)でrunの属性が失効しても処理を続けられるよう、先に値として取り出す
        requested = [dict(entry) for entry in run.requested]
        results: list[dict] = []
        quota_exceeded = False

        for entry in requested:
            diagram_id = uuid.UUID(entry["diagram_id"])
            if quota_exceeded:
                result = {
                    "subject": entry["subject"],
                    "diagram_id": entry["diagram_id"],
                    "outcome": "skipped",
                    "reason_code": "QUOTA_EXCEEDED",
                    "message": SKIPPED_MESSAGE,
                }
                await self._mark_failed(project_id, diagram_id, SKIPPED_MESSAGE)
            else:
                try:
                    await self._generate_into(project_id, diagram_id, llm=llm)
                    result = {
                        "subject": entry["subject"],
                        "diagram_id": entry["diagram_id"],
                        "outcome": "succeeded",
                        "reason_code": None,
                        "message": None,
                    }
                except Exception as exc:  # noqa: BLE001 -- 失敗理由を履歴に残し、次の対象へ進む
                    await self._session.rollback()
                    failure = classify_failure(exc)
                    logger.warning(
                        "uml_generation_failed",
                        project_id=str(project_id),
                        reason_code=failure.reason_code,
                        error_type=type(exc).__name__,
                    )
                    quota_exceeded = failure.reason_code == "QUOTA_EXCEEDED"
                    result = {
                        "subject": entry["subject"],
                        "diagram_id": entry["diagram_id"],
                        "outcome": "failed",
                        "reason_code": failure.reason_code,
                        "message": failure.message,
                    }
                    await self._mark_failed(project_id, diagram_id, failure.message)
            results.append(result)
            await self._save_results(project_id, run_id, results, finished=False)

        await self._save_results(project_id, run_id, results, finished=True)
        logger.info(
            "uml_generation_finished",
            project_id=str(project_id),
            succeeded=sum(1 for r in results if r["outcome"] == "succeeded"),
            total=len(results),
        )

    async def _generate_into(self, project_id: uuid.UUID, diagram_id: uuid.UUID, *, llm) -> None:
        """1件分を生成し、成功したら図を上書きしてcommitする。"""
        diagram = await self._diagrams.get_by_id(diagram_id, project_id=project_id)
        if diagram is None:
            raise UmlSubjectNotFoundError(f"Diagram {diagram_id} not found")
        document = await self._documents.get_current(
            project_id=project_id, doc_type=SOURCE_DOC_TYPE
        )
        if document is None:
            raise UmlSourceDocumentMissingError("内部設計書がありません")
        notation: NotationType = diagram.notation  # type: ignore[assignment]

        dfd_subject: DfdSubject | None = None
        existing_items: list[ExistingDataItem] = []
        if notation == "dfd":
            dfd_subject = next(
                (s for s in extract_dfd_subjects(document.content) if s.title == diagram.subject),
                None,
            )
            if dfd_subject is None:
                raise UmlSubjectNotFoundError(
                    f"処理が内部設計書に見つかりません: {diagram.subject}"
                )
            existing_items = [
                ExistingDataItem(name=item.name, field_names=[f["name"] for f in item.fields])
                for item in await self._data_items.list_for_project(project_id)
            ]
        er_tables = (diagram.scope or {}).get("tables") if notation == "er" else None

        messages = build_generation_messages(
            notation,
            document.content,
            dfd_subject=dfd_subject,
            er_tables=er_tables,
            data_items=existing_items,
        )
        output = await _invoke_structured(llm, GENERATION_SCHEMAS[notation], messages)

        if isinstance(output, DfdGenerationOutput):
            ids_by_name = await self._resolve_data_items(project_id, output)
            model = to_dfd(output, ids_by_name)
        else:
            model = to_semantic_model(output)

        diagram.semantic_model = model.model_dump(mode="json")
        diagram.layout_model = None
        diagram.status = "draft"
        diagram.version += 1
        diagram.source_doc_versions = {SOURCE_DOC_TYPE: document.version}
        diagram.generation_status = "completed"
        diagram.generation_error = None
        await self._session.commit()

    async def _resolve_data_items(
        self, project_id: uuid.UUID, output: DfdGenerationOutput
    ) -> dict[str, uuid.UUID]:
        """DFDの生成結果が使うデータ項目を名前でデータ辞書に解決する。既存の項目はそのまま使い
        (ユーザーが編集したフィールドを上書きしない)、無い項目だけを新規作成する。"""
        existing = {
            item.name: item.id for item in await self._data_items.list_for_project(project_id)
        }
        ids_by_name: dict[str, uuid.UUID] = {}
        for name, fields in required_data_items(output).items():
            if name in existing:
                ids_by_name[name] = existing[name]
            else:
                created = await self._data_items.create(
                    project_id=project_id, name=name, fields=[f.model_dump() for f in fields]
                )
                ids_by_name[name] = created.id
        return ids_by_name

    async def _resolve_scopes(
        self,
        project_id: uuid.UUID,
        notation: NotationType,
        requests: list[SubjectRequest],
        internal_design: str,
    ) -> dict[str, dict | None]:
        """対象ごとに、生成候補との照合と、図に保存するscope(ER部分図のテーブル選択)を決める。"""
        scopes: dict[str, dict | None] = {}
        if notation == "component":
            if [r.subject for r in requests] != [""]:
                raise UmlSubjectNotFoundError("コンポーネント図は対象を指定せずに生成してください")
            return {"": None}
        if notation == "dfd":
            titles = {s.title for s in extract_dfd_subjects(internal_design)}
            for request in requests:
                if request.subject not in titles:
                    raise UmlSubjectNotFoundError(
                        f"内部設計書の「処理別データフロー」に無い処理です: {request.subject}"
                    )
                scopes[request.subject] = None
            return scopes

        tables = extract_er_tables(internal_design)
        for request in requests:
            if request.subject == "":
                if len(tables) > MAX_ELEMENTS:
                    raise ErScopeRequiredError(
                        f"テーブルが{len(tables)}件あり、1枚の上限({MAX_ELEMENTS})を超えます。"
                        "テーブルを選んで部分図として生成してください。"
                    )
                scopes[""] = None
                continue
            selected = request.tables
            if selected is None:
                existing = await self._diagrams.get_by_subject(
                    project_id=project_id, notation="er", subject=request.subject
                )
                selected = (existing.scope or {}).get("tables") if existing else None
            if not selected:
                raise ErScopeRequiredError(
                    f"部分図「{request.subject}」の対象テーブルを選んでください"
                )
            if len(selected) > MAX_ELEMENTS:
                raise ErScopeRequiredError(
                    f"部分図に選べるテーブルは{MAX_ELEMENTS}件までです: {len(selected)}件"
                )
            unknown = [t for t in selected if t not in tables]
            if unknown:
                raise UmlSubjectNotFoundError(f"内部設計書に無いテーブルです: {', '.join(unknown)}")
            scopes[request.subject] = {"tables": list(selected)}
        return scopes

    async def _mark_failed(
        self, project_id: uuid.UUID, diagram_id: uuid.UUID, message: str
    ) -> None:
        diagram = await self._diagrams.get_by_id(diagram_id, project_id=project_id)
        if diagram is not None:
            diagram.generation_status = "failed"
            diagram.generation_error = message
        await self._session.commit()

    async def _save_results(
        self, project_id: uuid.UUID, run_id: uuid.UUID, results: list[dict], *, finished: bool
    ) -> None:
        run = await self._runs.get_by_id(run_id, project_id=project_id)
        if run is None:
            return
        # JSON列は要素の変更を検知しないため、新しいリストを代入する
        run.results = [dict(r) for r in results]
        if finished:
            succeeded = sum(1 for r in results if r["outcome"] == "succeeded")
            run.status = (
                "completed"
                if succeeded == len(results)
                else "failed"
                if succeeded == 0
                else "partial"
            )
            run.finished_at = datetime.now(UTC)
        await self._session.commit()


def _normalize_subjects(
    notation: NotationType, subjects: Sequence[SubjectRequest]
) -> list[SubjectRequest]:
    """対象の指定を正規化する。component/ERで省略した場合は全体('')1件とみなし、
    同じsubjectの重複指定は最初の1件にまとめる。DFDは1件以上の指定が必要。"""
    if not subjects:
        if notation == "dfd":
            raise UmlSubjectNotFoundError("DFDは生成する処理を1件以上選んでください")
        return [SubjectRequest(subject="")]
    unique: dict[str, SubjectRequest] = {}
    for request in subjects:
        unique.setdefault(request.subject, request)
    return list(unique.values())


async def _invoke_structured(llm, schema, messages) -> GenerationOutput:
    """構造化出力を`include_raw=True`で呼び、結果の解釈(トークン上限・解釈失敗の判別)を
    unwrap_structured_resultに任せる。リトライ・クォータ判定はinvoke_with_retryに集約する。"""
    structured_llm = llm.with_structured_output(schema, include_raw=True)

    async def _call() -> GenerationOutput:
        result = await structured_llm.ainvoke(messages)
        return unwrap_structured_result(result, schema)

    return await invoke_with_retry(_call, messages=messages)
