import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.gemini import extract_text_content, get_gemini_llm
from app.core.database import AsyncSessionLocal
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
from app.hearing import as_scale
from app.models.chat_history import ChatHistory
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import DOC_TYPES, GeneratedDocumentRepository
from app.repositories.hearing_point import HearingPointRepository
from app.repositories.project import ProjectRepository
from app.schemas.document import (
    AssumedRequirementRead,
    DocumentIssueRead,
    DocumentSummaryRead,
    ExternalDesignSummaryRead,
    GeneratedDocumentRead,
    RequirementsSummaryRead,
)
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    DocGenerationInProgressError,
    DocumentNotFoundError,
    LLMQuotaExceededError,
    RequirementNotAssumedError,
    RequirementNotFoundError,
)
from app.services.generation_staleness import is_stale
from app.services.llm_retry import invoke_structured, invoke_with_retry

logger = structlog.get_logger(__name__)

_DOC_TYPE_LABELS: dict[str, str] = {
    "requirements": "要件定義書",
    "external_design": "外部設計書",
    "internal_design": "内部設計書",
    "implementation_plan": "実装計画書",
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

# モード(projects.mode)ごとに生成する文書。詳細設計モードは要件定義・外部設計だけを生成し、
# 内部設計・実装計画に当たる部分は段階(docs/internal_design.md 3.3節「4. 詳細設計モード」)
# へ引き継ぐ。
# 並びは生成の順序で、_DOC_TYPE_INPUTSの前段が必ず先に来る。
DOC_TYPES_BY_MODE: dict[str, tuple[str, ...]] = {
    "simple": DOC_TYPES,
    "detailed": ("requirements", "external_design"),
}

_SELF_DIAGNOSIS_SYSTEM_PROMPT = (
    "あなたはレビュアーです。以下は今しがた生成された設計書です。"
    "各ドキュメントの不足・不明瞭な点を「最重要(実装着手に支障)」「中程度(後続の設計判断に影響)」"
    "「軽微(運用上の補足)」の3段階に分類し、日本語の箇条書きで指摘してください。"
    "指摘が無い場合は、その旨を簡潔に述べてください。"
)

# 生成の失敗・中断をチャットに残す文言(生の例外の文字列は利用者に見せず、ログにだけ残す)
_QUOTA_MESSAGE = "本日の利用上限に達しました。時間をおいて再度お試しください。"
_FAILED_MESSAGE = "設計書の生成に失敗しました。時間をおいて再度お試しください。"
_STALE_MESSAGE = (
    "設計書の生成が時間内に終わらなかったため、中断しました。もう一度生成してください。"
)


async def generate_documents(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    status_before_generation: str | None = None,
    *,
    llm=None,
) -> None:
    """`BackgroundTasks`から呼び出すエントリポイント。

    FastAPI 0.106以降、`yield`を使うDIのセッション(SessionDep)はbackground task実行"前"に
    クローズされるため、background task内でリクエストのセッションをそのまま使うことはできない。
    そのためこの関数はセッションを自前で開始・終了する(公式ドキュメントの推奨パターン:
    "pass identifiers ... retrieve the necessary objects inside the background task itself")。

    project_idに加えuser_idも受け取るのは、所有者チェック無しの専用メソッド(get_by_id_unscoped)を
    リポジトリに増やすのではなく、既存の所有者スコープ版get_by_idへ統一するため。project_id・
    user_idはどちらも値であり、セッションのようにリクエストのライフサイクルに紐づくものではない
    ため、background taskへ渡すこと自体に技術的な制約は無い。
    """
    async with AsyncSessionLocal() as session:
        await DocGeneratorService(session).generate(
            project_id, user_id, status_before_generation=status_before_generation, llm=llm
        )


class DocGeneratorService:
    """ヒアリング完了後の4種ドキュメント一括生成+自己診断を担当するサービス。"""

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション(background task用に独立して開始されたもの)
        self._session = session
        self._projects = ProjectRepository(session)
        self._chat_histories = ChatHistoryRepository(session)
        self._documents = GeneratedDocumentRepository(session)
        self._hearing_points = HearingPointRepository(session)
        self._stages = DesignStageService(session)

    async def request_generation(self, project: Project) -> str:
        """生成の要求を受け付け、`status`を`generating`にしてcommitする。戻り値は受け付ける前の状態
        (失敗したときに戻す先。バックグラウンドの`generate`へ渡す)。

        生成中なら`DocGenerationInProgressError`(409)にする。受け付けと同じリクエストの中で
        `generating`にするのは、バックグラウンドで切り替えると、切り替わる前の二度押しを
        すり抜けさせてしまうため。止まった「生成中」は先に回収する(`recover_if_stale`)。"""
        await self.recover_if_stale(project)
        if project.status == "generating":
            raise DocGenerationInProgressError(
                "設計書を生成しています。完了してから再度お試しください。"
            )
        status_before_generation = project.status
        project.status = "generating"
        await self._session.commit()
        # updated_at(生成を始めた時刻。回収の判定に使う)はDB側で決まるため読み直す
        await self._session.refresh(project)
        return status_before_generation

    async def recover_if_stale(self, project: Project, *, now: datetime | None = None) -> bool:
        """しきい値(15分)を超えて`generating`のままのプロジェクトを、生成前の状態へ戻す
        (app/services/generation_staleness.py)。戻した場合はTrue。

        生成前の状態は記録していないため、文書が既にあれば`revising`、無ければ`interviewing`にする
        (どちらも再度「生成する」を押せる状態)。中断したことはチャットに残す。"""
        if project.status != "generating":
            return False
        # updated_at はDB側で更新されるため、失効していれば読み直す(非同期では暗黙に読めない)
        await self._session.refresh(project, ["updated_at"])
        if not is_stale(project.updated_at, now or datetime.now(UTC)):
            return False
        has_documents = bool(await self._documents.list_current_for_project(project.id))
        project.status = "revising" if has_documents else "interviewing"
        await self._chat_histories.add(
            project_id=project.id, sender="others", message=_STALE_MESSAGE
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
        llm=None,
    ) -> None:
        """チャット全履歴をコンテキストに、モードごとの設計書(simpleは4種、detailedは要件定義・
        外部設計の2種。DOC_TYPES_BY_MODE)を生成し、続けて自己診断を行う。
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
        """
        project = await self._projects.get_by_id(project_id, user_id=user_id)
        if project is None:
            return

        if status_before_generation is None:
            status_before_generation = project.status
            project.status = "generating"
            await self._session.commit()

        try:
            llm = llm or get_gemini_llm()
            history = await self._chat_histories.list_for_project(project_id)
            transcript = _render_transcript(history)

            generated: dict[str, str] = {}
            requirements: RequirementsModel | None = None
            external: ExternalDesignModel | None = None
            internal: InternalDesignModel | None = None
            doc_types = DOC_TYPES_BY_MODE[project.mode]
            # 詳細設計モードの変更の影響は、生成の前に表示していた版と比べる
            previous_models: dict[str, dict | None] = {}
            for doc_type in doc_types:
                current = await self._documents.get_current(
                    project_id=project_id, doc_type=doc_type
                )
                previous_models[doc_type] = current.model if current is not None else None
            new_models: dict[str, dict | None] = {}
            for doc_type in doc_types:
                model: dict | None = None
                # 再生成を前の版の差分にとどめるため、上流の文書で変わった行を渡す
                changed = upstream_changes(
                    doc_type, previous_models, requirements, external, internal
                )
                if doc_type == "requirements":
                    content, requirements = await self._generate_requirements(
                        project, transcript, llm=llm
                    )
                    model = requirements.model_dump(mode="json")
                elif doc_type == "external_design":
                    content, external = await self._generate_external_design(
                        project, generated["requirements"], requirements, changed, llm=llm
                    )
                    model = external.model_dump(mode="json")
                elif doc_type == "internal_design":
                    content, internal = await self._generate_internal_design(
                        project, generated, external, changed, llm=llm
                    )
                    model = internal.model_dump(mode="json")
                else:
                    content, plan = await self._generate_implementation_plan(
                        project, generated, requirements, internal, changed, llm=llm
                    )
                    model = plan.model_dump(mode="json")
                await self._documents.create_version(
                    project_id=project_id, doc_type=doc_type, content=content, model=model
                )
                generated[doc_type] = content
                new_models[doc_type] = model
            for doc_type in doc_types:
                await self._stages.record_document_change(
                    project, doc_type, previous_models[doc_type], new_models[doc_type]
                )

            diagnosis = await self._self_diagnose(generated, llm=llm)
            await self._chat_histories.add(
                project_id=project_id, sender="others", message=diagnosis
            )


        except Exception as exc:  # noqa: BLE001 -- どの失敗要因も再試行可能な状態に戻す
            await self._session.rollback()
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
            # ユーザーに分かりやすい専用メッセージを表示する。
            await self._chat_histories.add(
                project_id=project_id,
                sender="others",
                message=(
                    _QUOTA_MESSAGE if isinstance(exc, LLMQuotaExceededError) else _FAILED_MESSAGE
                ),
            )
            await self._session.commit()
            return

        project.status = "completed"
        await self._session.commit()
        logger.info(
            "documents_generated", project_id=str(project_id), doc_count=len(generated)
        )


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
        self, project: Project, transcript: str, *, llm
    ) -> tuple[str, RequirementsModel]:
        """要件定義書を構造化出力で下書きし、model と md を作る。入力は会話・観点の表・前の版の
        要件(ID を引き継ぐため)。出どころは観点の状態を上限に直す(app/documents/requirements)。"""
        states = await self._hearing_points.load_states(project.id)
        scale = as_scale(project.hearing_scale)
        current = await self._documents.get_current(
            project_id=project.id, doc_type="requirements"
        )
        previous = parse_requirements_model(current.model if current else None)
        messages = build_requirements_messages(transcript, states, scale, previous)
        output = await invoke_structured(llm, RequirementsGenerationOutput, messages)
        model = build_model(to_draft(output), previous, states, scale)
        return render_requirements(model), model

    async def _generate_external_design(
        self,
        project: Project,
        requirements_md: str,
        requirements: RequirementsModel | None,
        changed_upstream: list[str],
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
        messages = build_external_design_messages(requirements_md, previous, changed_upstream)
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
            generated["requirements"], generated["external_design"], previous, changed_upstream
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
            generated["requirements"], generated["internal_design"], previous, changed_upstream
        )
        output = await invoke_structured(llm, ImplementationPlanGenerationOutput, messages)
        model = build_implementation_plan_model(
            to_implementation_plan_draft(output),
            previous,
            dataflow_ids=[f.id for f in internal.dataflows] if internal else [],
            requirements=requirements,
        )
        return render_implementation_plan(model), model

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
