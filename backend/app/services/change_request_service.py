import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.catalog import Purpose
from app.ai.llm.content import extract_text_content
from app.change_requests import (
    ACTIVE_STATUSES,
    CHECKABLE_STATUSES,
    COPYABLE_STATUSES,
    DECISION_ACTIONS,
    DOCUMENT_REFLECTABLE_STATUSES,
    INITIAL_STATUS,
    STAGE_REFLECTABLE_STATUSES,
    ChangeOptions,
    ChangeRequestAction,
    ChangeRequestFacts,
    ChangeRequestStatus,
    Comparison,
    DecisionFacts,
    DecisionSource,
    EventFacts,
    Explanation,
    OptionDraftCheck,
    OptionDraftOut,
    Overlap,
    Prediction,
    ReviewFacts,
    Scale,
    ScaleComparisonRow,
    ScaleSources,
    StageFacts,
    SummaryState,
    Target,
    TraceEntry,
    Verification,
    allowed_actions,
    applied_marks_from_json,
    build_cr_instruction,
    build_explanation,
    build_option_draft_messages,
    build_summary_messages,
    check_option_draft,
    checked_documents,
    clip_summary,
    compare,
    compare_scale,
    count_scale,
    document_items,
    draft_to_json,
    draft_to_options,
    first_applied_at,
    merge_applied_marks,
    needs_reconfirm,
    next_status,
    normalize_options,
    option_label,
    option_target_problems,
    options_from_json,
    options_to_json,
    origin_candidates_from_snapshots,
    overlaps,
    pending_stages,
    predict_from_snapshots,
    review_link_problem,
    snapshot_refs,
    stage_apply_versions,
    summary_state,
    target_ids,
    targets_from_json,
    targets_to_json,
    trace_entries,
    verification_of,
    with_confirmations,
    with_summary,
)
from app.change_requests.explanation import DOCUMENT_LABEL
from app.change_requests.prediction import STAGE_PREFIX
from app.detailed_design.impact import Mark, StageImpact
from app.detailed_design.impact.elements import parse_model
from app.detailed_design.plan import PlanModel
from app.detailed_design.procedure_doc import ProcedureDocModel
from app.models.baseline import Baseline
from app.models.change_request import ChangeRequest, ChangeRequestEvent
from app.models.change_request_option_draft import ChangeRequestOptionDraft
from app.models.change_request_summary import ChangeRequestSummary
from app.models.project import Project
from app.models.review import ReviewRun
from app.repositories.baseline import BaselineRepository
from app.repositories.change_request import ChangeRequestRepository
from app.repositories.change_request_option_draft import ChangeRequestOptionDraftRepository
from app.repositories.change_request_summary import ChangeRequestSummaryRepository
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.review import ReviewRunRepository
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestOptionsIn,
    ChangeRequestUpdate,
    ChangeRequestVerify,
    ChangeRequestWithdraw,
    DocumentChecksUpdate,
    change_request_label,
)
from app.services.baseline_service import (
    BaselineService,
    baseline_requirement_rows,
    baseline_snapshots,
)
from app.services.detailed_mode import require_detailed
from app.services.errors import (
    ChangeRequestBaselineMissingError,
    ChangeRequestInvalidTransitionError,
    ChangeRequestNotAppliedError,
    ChangeRequestNotFoundError,
    ChangeRequestNotReflectableError,
    ChangeRequestNotVerifiableError,
    ChangeRequestOptionDraftNotFoundError,
    ChangeRequestOptionInvalidError,
    ChangeRequestReviewNotLinkableError,
    ChangeRequestSummaryNotFoundError,
    ChangeRequestTargetUnknownError,
    ReviewRunNotFoundError,
)
from app.services.llm_resolver import LlmResolver, ResolvedLlm, injected_llm
from app.services.llm_retry import invoke_structured, invoke_with_retry

logger = structlog.get_logger(__name__)

# 反映した後の状態(予測と実際の印を比べられる)
APPLIED_STATUSES = frozenset({"applied", "verified"})


@dataclass(frozen=True)
class ChangeRequestView:
    """CR 1つと、詳細に出す周りの情報(重なり・写しの元と先・今できる操作)。"""

    change_request: ChangeRequest
    overlapping: list[Overlap]
    derived_from_number: int | None
    derived_numbers: list[int]
    allowed_actions: list[ChangeRequestAction]


@dataclass(frozen=True)
class OptionDraftView:
    """選択肢の下書きと、照合の結果・今の説明書の fingerprint(作った後に変わったかを見る)。"""

    change_request: ChangeRequest
    draft: ChangeRequestOptionDraft
    check: OptionDraftCheck
    current_fingerprint: str

    @property
    def options(self) -> ChangeOptions:
        return options_from_json(self.draft.options)


@dataclass(frozen=True)
class Reflection:
    """再生成に渡す CR と、修正の指示の文(app/change_requests/instruction.py)。"""

    change_request: ChangeRequest
    instruction: str


@dataclass(frozen=True)
class ExcludedItem:
    """基準の束に入っていない文書・段階(その要素は予測に出ない)と、束を作ったときの状態。"""

    key: str
    state: str


@dataclass(frozen=True)
class ImpactView:
    """CR 1つの影響の予測。`saved`は判断の時点で保存したものか(偽なら今求めたもの)。"""

    change_request: ChangeRequest
    prediction: Prediction
    saved: bool
    excluded: list[ExcludedItem]
    # 束に入っている段階の、束を作った時点の状態(規模の注記に使う)
    stage_states: dict[int, str]


@dataclass(frozen=True)
class ComparisonView:
    """CR 1つの、影響の予測と反映で付いた印の比較。`saved`は予測が判断の時点で保存したものか。"""

    change_request: ChangeRequest
    comparison: Comparison
    saved: bool
    scale: tuple[ScaleComparisonRow, ...] = ()


@dataclass(frozen=True)
class VerificationView:
    """CR 1つの検証の確かめ。`can_verify`は今、検証済みにできるか(反映済みで、未確認が無い)。"""

    change_request: ChangeRequest
    verification: Verification

    @property
    def can_verify(self) -> bool:
        return (
            next_status(self.change_request.status, "verify") is not None
            and self.verification.complete
        )


@dataclass(frozen=True)
class TraceView:
    """CR 1つの、受付から検証までをたどる記録。"""

    change_request: ChangeRequest
    entries: list[TraceEntry]


@dataclass(frozen=True)
class ExplanationView:
    """CR 1つの説明書(5節)と、任意の要約(無ければ None)。"""

    change_request: ChangeRequest
    explanation: Explanation
    summary: ChangeRequestSummary | None

    @property
    def summary_state(self) -> SummaryState | None:
        """要約の状態(今の5節と比べる)。要約が無ければ None。"""
        if self.summary is None:
            return None
        return summary_state(
            self.summary.status, self.summary.fingerprint, self.explanation.fingerprint
        )

    @property
    def summary_needs_reconfirm(self) -> bool:
        """確認済みの要約が、その後の説明書の変化で古くなったか(共有の前に確かめ直す)。"""
        if self.summary is None:
            return False
        return needs_reconfirm(
            self.summary.status, self.summary.fingerprint, self.explanation.fingerprint
        )

    @property
    def output(self) -> str:
        """出力する md(確認済みで古くない要約だけを載せる)。"""
        text = self.summary.text if self.summary is not None else None
        return with_summary(self.explanation, text if self.summary_state == "confirmed" else None)


class ChangeRequestService:
    """変更要求(CR)の作成・一覧・取得・更新と、状態の遷移(説明済み・合意 / 却下・取り下げ)・
    却下した CR の写し(docs/external_design.md 2.14節)。

    CR は合意済みの版(束)を基準にし、対象は基準の束の要件定義書にある要件 ID と、新しい要件を
    足す印。遷移の可否は app/change_requests/states.py の表で決め、遷移ごとに時系列へ1行残す。
    同じ要件を対象にする進行中の CR は止めず、詳細に重なりとして示す。影響の予測は基準の束の写しから
    求め(app/change_requests/prediction.py)、判断(合意・却下)の時点で CR に保存する。説明書は
    CR・束の写し・予測から決定的に組み立てる(app/change_requests/explanation.py)。反映の後は、
    反映で付いた印の今の確認の状態と文書の側の確認の一覧から、検証済みにできるかを決める
    (app/change_requests/verification.py)。詳細設計モードだけ。"""

    def __init__(self, session: AsyncSession, *, llm: Any = None) -> None:
        # llm: テストが差し替える要約の LLM(None なら利用者の生成の用途で解決する)
        self._session = session
        self._llm = llm
        self._change_requests = ChangeRequestRepository(session)
        self._baselines = BaselineRepository(session)
        self._summaries = ChangeRequestSummaryRepository(session)
        self._option_drafts = ChangeRequestOptionDraftRepository(session)

    async def create(
        self, project: Project, payload: ChangeRequestCreate, *, created_by: uuid.UUID | None
    ) -> ChangeRequestView:
        """CR を下書きで作る。基準の束は番号の束(省けば最新の束)。束が1つも無ければ
        ChangeRequestBaselineMissingError、番号の束が無ければ BaselineNotFoundError。"""
        require_detailed(project, "Change requests")
        baseline = await self._base_baseline(project, payload.baseline_number)
        targets = checked_targets(
            [Target(t.requirement_id, t.kind, t.proposed_text) for t in payload.targets],
            payload.adds_requirement,
            baseline,
        )
        origins = checked_origins(payload.origins, payload.adds_requirement, baseline)
        options = checked_options(
            payload.options.to_options() if payload.options is not None else ChangeOptions(),
            targets,
            payload.adds_requirement,
        )
        change_request = await self._change_requests.create(
            project_id=project.id,
            number=await self._change_requests.next_number(project.id),
            baseline_id=baseline.id,
            title=payload.title,
            content=payload.content,
            background_source=payload.background_source,
            background_date=payload.background_date,
            background_memo=payload.background_memo,
            targets=targets_to_json(targets),
            adds_requirement=payload.adds_requirement,
            origins=origins,
            options=options_to_json(options),
            status=INITIAL_STATUS,
            action="create",
            created_by=created_by,
        )
        await self._session.commit()
        logger.info(
            "change_request_created", project_id=str(project.id), number=change_request.number
        )
        return await self._view(change_request)

    async def list_for_project(self, project: Project) -> list[ChangeRequest]:
        """プロジェクトの CR を新しい順に返す(詳細設計モードだけ)。"""
        require_detailed(project, "Change requests")
        return await self._change_requests.list_for_project(project.id)

    async def get(self, project: Project, number: int) -> ChangeRequestView:
        """番号の CR(詳細)。無ければ ChangeRequestNotFoundError。"""
        return await self._view(await self._load(project, number))

    async def update(
        self,
        project: Project,
        number: int,
        payload: ChangeRequestUpdate,
        *,
        actor_id: uuid.UUID | None,
    ) -> ChangeRequestView:
        """CR の中身を直す(送った項目だけ)。下書き・説明済みだけで、説明済みで直すと下書きに
        戻る。対象を変えたら、基準の束の要件定義書で確かめ直す。予測の起点は、送ったときと新しい
        要件を足す印を変えたときに、基準の束で確かめ直す。選択肢(案)は、送ったときと対象・新しい
        要件を足す印を変えたときに、CR の対象で確かめ直す。値の変わった項目があれば
        (下書きのままでも)時系列に「直した」の1行を足し、`refs`に直した項目の名前を残す
        (前後の値は持たない)。値の変わらない保存は行を足さない。"""
        change_request = await self._load(project, number)
        to_status = self._next(change_request, "edit")
        changes = payload.model_dump(exclude_unset=True)
        adds = changes.get("adds_requirement")
        adds_requirement = change_request.adds_requirement if adds is None else adds
        targets = targets_from_json(change_request.targets)
        if "targets" in changes or "adds_requirement" in changes:
            targets = (
                targets_from_json(change_request.targets)
                if payload.targets is None
                else [Target(t.requirement_id, t.kind, t.proposed_text) for t in payload.targets]
            )
            targets = checked_targets(
                targets, adds_requirement, await self._baseline_with_items(change_request)
            )
            changes["targets"] = targets_to_json(targets)
        if payload.origins is None:
            changes.pop("origins", None)  # null は変えない(下の確かめ直しでだけ入る)
        if payload.origins is not None or "adds_requirement" in changes:
            keys = origin_keys_of(change_request) if payload.origins is None else payload.origins
            changes["origins"] = checked_origins(
                keys, adds_requirement, await self._baseline_with_items(change_request)
            )
        if payload.options is None:
            changes.pop("options", None)  # null は変えない(下の確かめ直しでだけ入る)
        if payload.options is not None or "targets" in changes or "adds_requirement" in changes:
            options = (
                options_from_json(change_request.options)
                if payload.options is None
                else payload.options.to_options()
            )
            changes["options"] = options_to_json(
                checked_options(options, targets, adds_requirement)
            )
        edited: list[str] = []
        for field, value in changes.items():
            # 必須の項目に null を送ったときは、変えない(背景の日付は null で消せる。起点・案は
            # 確かめた結果が None = 起点なし・案なし)
            if value is None and field not in ("background_date", "origins", "options"):
                continue
            if getattr(change_request, field) != value:
                edited.append(field)
            setattr(change_request, field, value)
        if edited or to_status != change_request.status:
            await self._change_requests.add_event(
                change_request,
                action="edit",
                to_status=to_status,
                actor_id=actor_id,
                refs={"fields": edited},
            )
        else:
            change_request.updated_at = datetime.now(UTC)
        await self._session.commit()
        return await self._view(change_request)

    async def explain(
        self, project: Project, number: int, *, actor_id: uuid.UUID | None
    ) -> ChangeRequestView:
        """下書きの CR を説明済みにする(クライアントに説明した)。"""
        change_request = await self._load(project, number)
        await self.record_explain(change_request, actor_id=actor_id)
        await self._session.commit()
        return await self._view(change_request)

    async def record_explain(
        self, change_request: ChangeRequest, *, actor_id: uuid.UUID | None, note: str = ""
    ) -> ChangeRequestEvent:
        """下書きの CR を説明済みにし、その時点の説明書の md と指紋を時系列の行に残す(共有リンクの
        「前回から変わった点」の材料。app/change_requests/sharing.py)。下書きでなければ
        ChangeRequestInvalidTransitionError。共有リンクの作成と同じトランザクションで使うため、
        commitしない。"""
        event = await self._change_requests.add_event(
            change_request,
            action="explain",
            to_status=self._next(change_request, "explain"),
            actor_id=actor_id,
            note=note,
        )
        # 説明済みにした後の説明書(頭の「CR の状態」も説明済み)を写す
        explanation = await self._explanation(change_request)
        event.refs = snapshot_refs(explanation.markdown, explanation.fingerprint)
        await self._session.flush()
        return event

    async def decide(
        self,
        project: Project,
        number: int,
        payload: ChangeRequestDecision,
        *,
        actor_id: uuid.UUID | None,
    ) -> ChangeRequestView:
        """説明済みの CR を合意か却下にする(開発者が記録する。中身は record_decision)。"""
        change_request = await self._load(project, number)
        await self.record_decision(change_request, payload, actor_id=actor_id, source="developer")
        await self._session.commit()
        logger.info(
            "change_request_decided",
            project_id=str(project.id),
            number=number,
            decision=payload.decision,
        )
        return await self._view(change_request)

    async def record_decision(
        self,
        change_request: ChangeRequest,
        payload: ChangeRequestDecision,
        *,
        actor_id: uuid.UUID | None,
        source: DecisionSource,
        fingerprint: str | None = None,
        refs: dict[str, Any] | None = None,
    ) -> None:
        """説明済みの CR を合意か却下にし、合意の記録(相手・日時・メモ・理由・記録した人・出どころ・
        判断したときの説明書の指紋)と、その時点の影響の予測を残す。案のある CR の合意では選んだ案の
        対象を CR の対象にし(前の対象は時系列の行に残す)、予測はその対象で求める。指紋を渡さなければ
        今の説明書の指紋を残す。`refs`は時系列の行に足すもの(共有リンクの出どころなど)。説明済みで
        なければ ChangeRequestInvalidTransitionError。共有リンクの判断と同じトランザクションで使う
        ため、commitしない。"""
        action = DECISION_ACTIONS[payload.decision]
        to_status = self._next(change_request, action)
        if fingerprint is None:
            fingerprint = (await self._explanation(change_request)).fingerprint
        event_refs = dict(refs or {})
        chosen = chosen_option(change_request, payload)
        if chosen is not None:
            event_refs |= {"option": chosen, "targets": list(change_request.targets)}
            option = options_from_json(change_request.options).option(chosen)
            change_request.targets = targets_to_json(option.targets)
        change_request.decision_option = chosen
        # 判断のときに示した影響の範囲を残す(後でたどり方の規則を直しても変わらないように)
        baseline = await self._baseline_with_items(change_request)
        change_request.predicted_impact = predict_for(change_request, baseline).to_dict()
        change_request.decision_counterpart = payload.counterpart
        change_request.decided_at = payload.decided_at
        change_request.decision_memo = payload.memo
        change_request.decision_reason = payload.reason
        change_request.decision_recorded_by = actor_id
        change_request.decision_source = source
        change_request.decision_fingerprint = fingerprint
        await self._change_requests.add_event(
            change_request,
            action=action,
            to_status=to_status,
            actor_id=actor_id,
            refs=event_refs or None,
        )

    async def withdraw(
        self,
        project: Project,
        number: int,
        payload: ChangeRequestWithdraw,
        *,
        actor_id: uuid.UUID | None,
    ) -> ChangeRequestView:
        """合意の前(下書き・説明済み)の CR を取り下げる。理由は時系列に残す。"""
        change_request = await self._load(project, number)
        await self._change_requests.add_event(
            change_request,
            action="withdraw",
            to_status=self._next(change_request, "withdraw"),
            actor_id=actor_id,
            note=payload.reason.strip(),
        )
        await self._session.commit()
        return await self._view(change_request)

    async def copy_rejected(
        self, project: Project, number: int, *, created_by: uuid.UUID | None
    ) -> ChangeRequestView:
        """却下した CR を写して、新しい番号の CR を下書きで作る(中身・背景・対象・案・基準の束)。
        元の CR は却下と合意の記録のまま残し、時系列に写した先を1行足す。"""
        source = await self._load(project, number)
        if source.status not in COPYABLE_STATUSES:
            raise ChangeRequestInvalidTransitionError(
                f"CR-{number} cannot be copied (status={source.status})"
            )
        copied = await self._change_requests.create(
            project_id=project.id,
            number=await self._change_requests.next_number(project.id),
            baseline_id=source.baseline_id,
            title=source.title,
            content=source.content,
            background_source=source.background_source,
            background_date=source.background_date,
            background_memo=source.background_memo,
            targets=list(source.targets),
            adds_requirement=source.adds_requirement,
            origins=list(source.origins) if source.origins else None,
            options=dict(source.options) if source.options else None,
            status=INITIAL_STATUS,
            action="copy",
            derived_from_id=source.id,
            created_by=created_by,
            note=f"{change_request_label(source.number)}(却下)から写した",
        )
        await self._change_requests.add_event(
            source,
            action="copied",
            to_status=source.status,
            actor_id=created_by,
            note=f"{change_request_label(copied.number)} に写した",
        )
        await self._session.commit()
        logger.info(
            "change_request_copied",
            project_id=str(project.id),
            source=source.number,
            number=copied.number,
        )
        return await self._view(copied)

    async def impact(self, project: Project, number: int) -> ImpactView:
        """CR の影響の予測。判断の後は保存したもの、判断の前(と取り下げ)は基準の束から求める。"""
        change_request = await self._load(project, number)
        _, view = await self._impact_with_baseline(change_request)
        return view

    async def explanation(self, project: Project, number: int) -> ExplanationView:
        """CR の説明書(5節)と要約。予測は影響の API と同じもの(判断の後は保存したもの)を使う。"""
        return await self._explanation_view(await self._load(project, number))

    async def explanation_of(self, change_request: ChangeRequest) -> ExplanationView:
        """読み込んだ CR の説明書と要約(共有リンクの公開の入口が、リンクから引いた CR で使う。
        モードの確かめは呼ぶ側)。"""
        return await self._explanation_view(change_request)

    async def load(self, project: Project, number: int) -> ChangeRequest:
        """番号の CR(詳細設計モードだけ)。無ければ ChangeRequestNotFoundError。"""
        return await self._load(project, number)

    async def generate_summary(
        self, project: Project, number: int, *, user_id: uuid.UUID
    ) -> ExplanationView:
        """説明書の5節から、LLM(利用者の生成の用途)でクライアント向けの要約を作る。要約は
        未確認ででき、前の要約(直した文・確かめた記録)は上書きする。どの状態の CR でも作れる。"""
        change_request = await self._load(project, number)
        explanation = await self._explanation(change_request)
        resolved = await self._summary_llm(user_id)
        messages = build_summary_messages(explanation.markdown)

        async def _call() -> str:
            response = await resolved.llm.ainvoke(messages)
            return extract_text_content(response.content)

        text = clip_summary(await invoke_with_retry(_call, messages=messages))
        summary = await self._summaries.replace(
            change_request_id=change_request.id,
            text=text,
            fingerprint=explanation.fingerprint,
            status="unconfirmed",
            generated_model=resolved.model,
            generated_by=user_id,
        )
        await self._session.commit()
        logger.info(
            "change_request_summary_generated",
            project_id=str(project.id),
            number=number,
            model=resolved.model,
        )
        return ExplanationView(change_request, explanation, summary)

    async def edit_summary(self, project: Project, number: int, text: str) -> ExplanationView:
        """要約の文を直す。直すと未確認に戻る(元の LLM の文は残す)。"""
        change_request = await self._load(project, number)
        summary = await self._require_summary(change_request)
        summary.text = clip_summary(text)
        summary.status = "unconfirmed"
        summary.edited_at = datetime.now(UTC)
        summary.confirmed_by = None
        summary.confirmed_at = None
        await self._session.commit()
        return await self._explanation_view(change_request)

    async def confirm_summary(
        self, project: Project, number: int, *, actor_id: uuid.UUID | None
    ) -> ExplanationView:
        """要約を確かめた(誰が・いつ)と記録し、今の5節の fingerprint に合わせる(古い要約も、
        今の5節と見比べて確かめれば載る)。照合の結果では止めない(人が判断する)。"""
        change_request = await self._load(project, number)
        summary = await self._require_summary(change_request)
        explanation = await self._explanation(change_request)
        summary.status = "confirmed"
        summary.fingerprint = explanation.fingerprint
        summary.confirmed_by = actor_id
        summary.confirmed_at = datetime.now(UTC)
        await self._session.commit()
        return ExplanationView(change_request, explanation, summary)

    async def generate_option_draft(
        self, project: Project, number: int, *, user_id: uuid.UUID
    ) -> OptionDraftView:
        """説明書から、LLM(要約と同じ生成の用途)で選択肢の下書きを作る。下書きは未確認ででき、
        前の下書き(取り込んだ記録も)は上書きする。案を直せる状態(下書き・説明済み)だけ。"""
        change_request = await self._load(project, number)
        self._next(change_request, "edit")
        explanation = await self._explanation(change_request)
        resolved = await self._summary_llm(user_id)
        out = await invoke_structured(
            resolved.llm, OptionDraftOut, build_option_draft_messages(explanation.markdown)
        )
        await self._option_drafts.replace(
            change_request_id=change_request.id,
            options=draft_to_json(draft_to_options(out)),
            fingerprint=explanation.fingerprint,
            status="unconfirmed",
            generated_model=resolved.model,
            generated_by=user_id,
        )
        await self._session.commit()
        logger.info(
            "change_request_option_draft_generated",
            project_id=str(project.id),
            number=number,
            model=resolved.model,
        )
        return await self._option_draft_view(change_request)

    async def option_draft(self, project: Project, number: int) -> OptionDraftView:
        """選択肢の下書きと照合の結果。下書きが無ければ ChangeRequestOptionDraftNotFoundError。"""
        return await self._option_draft_view(await self._load(project, number))

    async def adopt_option_draft(
        self, project: Project, number: int, *, actor_id: uuid.UUID | None
    ) -> ChangeRequestView:
        """下書きの案を CR の案に写す(案を直す更新と同じく、時系列に「直した」を残し、説明済みなら
        下書きに戻る)。下書きは取り込み済みにする。案の規則か CR の対象に合わなければ
        ChangeRequestOptionInvalidError。"""
        view = await self._option_draft_view(await self._load(project, number))
        if not view.check.adoptable:
            raise ChangeRequestOptionInvalidError(" / ".join(view.check.problems))
        view.draft.status = "adopted"
        view.draft.adopted_by = actor_id
        view.draft.adopted_at = datetime.now(UTC)
        payload = ChangeRequestUpdate(
            options=ChangeRequestOptionsIn.model_validate(
                options_to_json(normalize_options(view.options))
            )
        )
        return await self.update(project, number, payload, actor_id=actor_id)

    async def comparison(self, project: Project, number: int) -> ComparisonView:
        """反映した CR の、影響の予測(判断の時点で保存したもの)と反映で付いた印の比較。反映の前は
        ChangeRequestNotAppliedError。印を付ける段階は、今、一度でも承認した段階。印の確認の状態は
        今の状態(承認で消えた印は確認済み)、文書の側は確認の一覧の状態。"""
        change_request = self._require_applied(await self._load(project, number))
        view, verification, marked = await self._verification(project, change_request)
        applied = with_confirmations(
            applied_marks_from_json(change_request.applied_impact), verification.marks
        )
        comparison = compare(
            view.prediction, applied, marked, change_request.document_checks or []
        )
        scale = compare_scale(
            (await self._explanation(change_request)).scale,
            await self._actual_scale(project, change_request, applied),
            [item.key for item in view.prediction.items],
            change_request.document_checks or [],
        )
        return ComparisonView(change_request, comparison, saved=view.saved, scale=scale)

    async def _actual_scale(
        self, project: Project, change_request: ChangeRequest, applied: Sequence[Any]
    ) -> Scale:
        """反映で付いた印の要素を、今の設計(段階7・8 の今の内容・今のテスト仕様書)で数える。"""
        rows = await DesignStageRepository(self._session).list_for_project(project.id)
        models = {row.stage: row.model for row in rows if row.model}
        test_spec = await GeneratedDocumentRepository(self._session).get_current(
            project_id=project.id, doc_type="test_spec"
        )
        sources = ScaleSources(
            plan=parse_model(PlanModel, models.get(7)),
            procedure_docs=parse_model(ProcedureDocModel, models.get(8)),
            test_spec=test_spec.model if test_spec is not None else None,
            has_external_design=True,
            missing_stages=frozenset(s for s in (1, 7) if s not in models),
        )
        return count_scale(
            [(mark.key, mark.label) for mark in applied],
            sources,
            requirement_ids=target_ids(targets_from_json(change_request.targets)),
        )

    async def verification(self, project: Project, number: int) -> VerificationView:
        """反映した CR の検証の確かめ(印の今の確認の状態と文書の側の確認の一覧)。反映の前は
        ChangeRequestNotAppliedError。"""
        change_request = self._require_applied(await self._load(project, number))
        _, verification, _ = await self._verification(project, change_request)
        return VerificationView(change_request, verification)

    async def check_documents(
        self, project: Project, number: int, payload: DocumentChecksUpdate
    ) -> VerificationView:
        """文書の側の確認の項目を、確認済み(または未確認)にする。反映済みの間だけ(ほかは
        ChangeRequestInvalidTransitionError)。予測に無いキーは無視する。"""
        change_request = await self._load(project, number)
        if change_request.status not in CHECKABLE_STATUSES:
            raise ChangeRequestInvalidTransitionError(
                f"CR-{number} cannot check documents (status={change_request.status})"
            )
        _, view = await self._impact_with_baseline(change_request)
        known = [item.key for item in document_items(view.prediction, [])]
        change_request.document_checks = checked_documents(
            change_request.document_checks, payload.keys, payload.reviewed, known
        )
        change_request.updated_at = datetime.now(UTC)
        await self._session.commit()
        return await self.verification(project, number)

    async def verify(
        self,
        project: Project,
        number: int,
        payload: ChangeRequestVerify,
        *,
        actor_id: uuid.UUID | None,
    ) -> ChangeRequestView:
        """反映済みの CR を検証済みにする。反映で付いた印と文書の側の確認に未確認が残っていれば
        ChangeRequestNotVerifiableError。時系列に確かめた数を残す。"""
        change_request = await self._load(project, number)
        to_status = self._next(change_request, "verify")
        _, verification, _ = await self._verification(project, change_request)
        if not verification.complete:
            pending = "".join(f"・段階{n}の承認" for n in verification.pending_stages)
            raise ChangeRequestNotVerifiableError(
                f"{change_request_label(number)} に未確認が残っています(印 "
                f"{verification.open_marks} 件・文書の側 {verification.open_documents} 件"
                f"{pending})。"
            )
        await self._change_requests.add_event(
            change_request,
            action="verify",
            to_status=to_status,
            actor_id=actor_id,
            note=payload.note.strip(),
            refs={
                "mark_count": len(verification.marks),
                "document_count": len(verification.documents),
                "review_run_ids": [str(r.review_run_id) for r in change_request.reviews],
            },
        )
        await self._session.commit()
        logger.info("change_request_verified", project_id=str(project.id), number=number)
        return await self._view(change_request)

    async def review_candidates(self, project: Project, number: int) -> list[ReviewRun]:
        """CR に結べるレビューの回(反映の後に流して成功し、まだ結んでいないもの。新しい順)。
        反映済みでなければ空。"""
        change_request = await self._load(project, number)
        runs = await ReviewRunRepository(self._session).list_for_project(project.id)
        return [run for run in runs if self._link_problem(change_request, run) is None]

    async def link_review(
        self,
        project: Project,
        number: int,
        review_run_id: uuid.UUID,
        *,
        actor_id: uuid.UUID | None,
    ) -> ChangeRequestView:
        """CR にレビューの回を結び、時系列に1行(状態は変えない)残す。回がプロジェクトに無ければ
        ReviewRunNotFoundError、結べなければ ChangeRequestReviewNotLinkableError。"""
        change_request = await self._load(project, number)
        run = await ReviewRunRepository(self._session).find_one(
            id=review_run_id, project_id=project.id
        )
        if run is None:
            raise ReviewRunNotFoundError(f"Review run {review_run_id} not found")
        problem = self._link_problem(change_request, run)
        if problem is not None:
            raise ChangeRequestReviewNotLinkableError(problem)
        await self._change_requests.link_review(change_request, run, linked_by=actor_id)
        await self._change_requests.add_event(
            change_request,
            action="link_review",
            to_status=change_request.status,
            actor_id=actor_id,
            note=f"設計レビュー({run.gate}・{run.model})を結んだ",
            refs={"review_run_id": str(run.id), "gate": run.gate, "model": run.model},
        )
        await self._session.commit()
        return await self._view(change_request)

    def _link_problem(self, change_request: ChangeRequest, run: ReviewRun) -> str | None:
        return review_link_problem(
            change_request.status,
            first_applied_at((e.action, e.occurred_at) for e in change_request.events),
            run.status,
            run.started_at,
            already_linked=any(r.review_run_id == run.id for r in change_request.reviews),
        )

    async def trace(self, project: Project, number: int) -> TraceView:
        """CR の時系列を、指す先(作った文書の版が今も残っているか・段階の版・結んだレビュー・
        入れた束)と一緒に返す。"""
        change_request = await self._load(project, number)
        documents = GeneratedDocumentRepository(self._session)
        doc_types = {
            str(item["doc_type"])
            for event in change_request.events
            for item in (event.refs or {}).get("documents") or []
            if isinstance(item, dict) and "doc_type" in item
        }
        available = {
            doc_type: {
                d.version
                for d in await documents.list_versions(project_id=project.id, doc_type=doc_type)
            }
            for doc_type in doc_types
        }
        reviews = {
            str(link.review_run_id): review_facts(link.review_run)
            for link in change_request.reviews
        }
        events = [
            EventFacts(
                e.action, e.from_status, e.to_status, e.occurred_at, e.actor_id, e.note, e.refs
            )
            for e in change_request.events
        ]
        decision = DecisionFacts(
            change_request.decision_counterpart,
            change_request.decision_memo,
            change_request.decision_reason,
            chosen_option_name(change_request),
            change_request.decision_source,
        )
        return TraceView(change_request, trace_entries(events, available, reviews, decision))

    async def reflectable(
        self, project: Project, number: int, *, stage: bool = False
    ) -> ChangeRequest:
        """再生成に渡せる CR(受け付けのときの確かめ)。無ければ ChangeRequestNotFoundError、文書は
        合意・反映済み、段階(`stage`)は反映済みでなければ ChangeRequestNotReflectableError。"""
        change_request = await self._load(project, number)
        statuses = STAGE_REFLECTABLE_STATUSES if stage else DOCUMENT_REFLECTABLE_STATUSES
        if change_request.status not in statuses:
            where = "文書へ反映した後(反映済み)" if stage else "合意した後"
            raise ChangeRequestNotReflectableError(
                f"{change_request_label(number)} は今の状態では反映できません。{where}の CR を"
                "渡してください。"
            )
        return change_request

    async def reflection(
        self, project_id: uuid.UUID, number: int | None, *, stage: bool = False
    ) -> Reflection | None:
        """バックグラウンドの生成に渡す CR と指示。受け付けの後に消えた・反映できない状態に
        なった CR は渡さない(None。指摘と同じく、生成は CR なしで続ける)。"""
        if number is None:
            return None
        change_request = await self._change_requests.get_by_number(
            project_id=project_id, number=number
        )
        statuses = STAGE_REFLECTABLE_STATUSES if stage else DOCUMENT_REFLECTABLE_STATUSES
        if change_request is None or change_request.status not in statuses:
            return None
        facts = facts_of(change_request, change_request.baseline.number)
        return Reflection(change_request, build_cr_instruction(facts))

    async def record_document_apply(
        self,
        change_request: ChangeRequest,
        *,
        documents: Sequence[tuple[str, int]],
        model: str | None,
        marks: Sequence[Mark],
        actor_id: uuid.UUID | None,
    ) -> None:
        """CR を渡した文書の再生成が終わったことを記録する。反映済みにし(やり直しは反映済みのまま)、
        時系列に作った版(`documents`=(文書の種類, 版))とモデル名を残し、付いた印を写しに重ねる。
        生成と同じトランザクションで使うため、commitしない。"""
        change_request.applied_impact = merge_applied_marks(
            change_request.applied_impact, marks
        )
        versions = "・".join(f"{DOCUMENT_LABEL.get(t, t)} v{v}" for t, v in documents)
        await self._change_requests.add_event(
            change_request,
            action="apply",
            to_status=self._next(change_request, "apply"),
            actor_id=actor_id,
            note=f"{versions} を作り直した",
            refs={
                "documents": [{"doc_type": t, "version": v} for t, v in documents],
                "model": model,
            },
        )

    async def record_stage_apply(
        self,
        change_request: ChangeRequest,
        *,
        stage: int,
        version: int,
        model: str | None,
        actor_id: uuid.UUID | None,
    ) -> None:
        """CR を渡した段階の生成が終わったことを、時系列に1行(反映済みのまま。段階・版・モデル名)
        残す。生成と同じトランザクションで使うため、commitしない。"""
        await self._change_requests.add_event(
            change_request,
            action="apply",
            to_status=self._next(change_request, "apply"),
            actor_id=actor_id,
            note=f"段階{stage}の下書きを作り直した",
            refs={"stage": stage, "version": version, "model": model},
        )

    def _require_applied(self, change_request: ChangeRequest) -> ChangeRequest:
        if change_request.status not in APPLIED_STATUSES:
            raise ChangeRequestNotAppliedError(
                f"{change_request_label(change_request.number)} はまだ反映していません。"
            )
        return change_request

    async def _verification(
        self, project: Project, change_request: ChangeRequest
    ) -> tuple[ImpactView, Verification, set[int]]:
        """予測(保存したもの)・検証の確かめと、印を付ける段階(一度でも承認した段階)。"""
        _, view = await self._impact_with_baseline(change_request)
        rows = await DesignStageRepository(self._session).list_for_project(project.id)
        current = {row.stage: StageImpact.from_dict(row.impact) for row in rows}
        pending = pending_stages(
            change_request.id,
            stage_apply_versions((e.action, e.refs) for e in change_request.events),
            [
                StageFacts(row.stage, row.generation_change_request_id, row.approved_version)
                for row in rows
            ],
        )
        verification = verification_of(
            applied_marks_from_json(change_request.applied_impact),
            current,
            change_request.number,
            view.prediction,
            change_request.document_checks or [],
            pending,
        )
        marked = {row.stage for row in rows if row.approved_version is not None}
        return view, verification, marked

    async def _explanation_view(self, change_request: ChangeRequest) -> ExplanationView:
        return ExplanationView(
            change_request=change_request,
            explanation=await self._explanation(change_request),
            summary=await self._summaries.get_for(change_request.id),
        )

    async def _require_summary(self, change_request: ChangeRequest) -> ChangeRequestSummary:
        summary = await self._summaries.get_for(change_request.id)
        if summary is None:
            raise ChangeRequestSummaryNotFoundError(
                f"CR-{change_request.number} has no summary yet"
            )
        return summary

    async def _option_draft_view(self, change_request: ChangeRequest) -> OptionDraftView:
        draft = await self._option_drafts.get_for(change_request.id)
        if draft is None:
            raise ChangeRequestOptionDraftNotFoundError(
                f"CR-{change_request.number} has no option draft yet"
            )
        explanation = await self._explanation(change_request)
        return OptionDraftView(
            change_request=change_request,
            draft=draft,
            check=check_option_draft(
                options_from_json(draft.options),
                explanation,
                targets_from_json(change_request.targets),
                change_request.adds_requirement,
            ),
            current_fingerprint=explanation.fingerprint,
        )

    async def _summary_llm(self, user_id: uuid.UUID) -> ResolvedLlm:
        if self._llm is not None:
            return injected_llm(self._llm, Purpose.GENERATION)
        return await LlmResolver(self._session).resolve(user_id, Purpose.GENERATION)

    async def _explanation(self, change_request: ChangeRequest) -> Explanation:
        baseline, view = await self._impact_with_baseline(change_request)
        return build_explanation(
            facts_of(change_request, baseline.number),
            view.prediction,
            {item.key: item.model for item in baseline.items if item.included},
            [(e.key, e.state) for e in view.excluded],
            view.stage_states,
        )

    async def _impact_with_baseline(
        self, change_request: ChangeRequest
    ) -> tuple[Baseline, ImpactView]:
        baseline = await self._baseline_with_items(change_request)
        saved = change_request.predicted_impact
        prediction = (
            Prediction.from_dict(saved)
            if saved is not None
            else predict_for(change_request, baseline)
        )
        return baseline, ImpactView(
            change_request=change_request,
            prediction=prediction,
            saved=saved is not None,
            excluded=[
                ExcludedItem(key=item.key, state=item.state)
                for item in baseline.items
                if not item.included
            ],
            stage_states={
                int(item.key.removeprefix(STAGE_PREFIX)): item.state
                for item in baseline.items
                if item.included and item.key.startswith(STAGE_PREFIX)
            },
        )

    async def _base_baseline(self, project: Project, number: int | None) -> Baseline:
        if number is not None:
            return await BaselineService(self._session).get(project, number)
        latest = await self._baselines.latest(project.id)
        if latest is None:
            raise ChangeRequestBaselineMissingError(f"No baseline in project {project.id}")
        return latest

    async def _baseline_with_items(self, change_request: ChangeRequest) -> Baseline:
        """CR の基準の束を、写し付きで読む(CR と一緒に読んだ束は写しを読んでいないため)。"""
        baseline = await self._baselines.find_one(id=change_request.baseline_id)
        assert baseline is not None  # 束は消えない(CR はプロジェクトの削除でだけ一緒に消える)
        return baseline

    async def _load(self, project: Project, number: int) -> ChangeRequest:
        # CR ごとの操作と読み出し(予測・比較・検証・説明書・たどる記録)はみなここを通る
        require_detailed(project, "Change requests")
        change_request = await self._change_requests.get_by_number(
            project_id=project.id, number=number
        )
        if change_request is None:
            raise ChangeRequestNotFoundError(f"Change request CR-{number} not found")
        return change_request

    def _next(self, change_request: ChangeRequest, action: ChangeRequestAction) -> str:
        to_status = next_status(change_request.status, action)
        if to_status is None:
            raise ChangeRequestInvalidTransitionError(
                f"CR-{change_request.number} cannot {action} (status={change_request.status})"
            )
        return to_status

    async def _view(self, change_request: ChangeRequest) -> ChangeRequestView:
        overlapping: list[Overlap] = []
        if change_request.status in ACTIVE_STATUSES:
            others = await self._change_requests.list_by_statuses(
                change_request.project_id, ACTIVE_STATUSES
            )
            overlapping = overlaps(
                target_ids(targets_from_json(change_request.targets)),
                (
                    (other.number, target_ids(targets_from_json(other.targets)))
                    for other in others
                    if other.id != change_request.id
                ),
            )
        derived_from_number = None
        if change_request.derived_from_id is not None:
            source = await self._change_requests.get_by_id(change_request.derived_from_id)
            derived_from_number = source.number if source is not None else None
        derived = await self._change_requests.find_derived(change_request.id)
        status: ChangeRequestStatus = change_request.status  # type: ignore[assignment]
        return ChangeRequestView(
            change_request=change_request,
            overlapping=overlapping,
            derived_from_number=derived_from_number,
            derived_numbers=[c.number for c in derived],
            allowed_actions=allowed_actions(status),
        )


def review_facts(run: ReviewRun) -> ReviewFacts:
    """結んだレビューの回の要点(指摘は読み込み済みであること)。"""
    return ReviewFacts(
        review_run_id=str(run.id),
        gate=run.gate,
        model=run.model,
        status=run.status,
        findings=len(run.findings),
        adopted=sum(1 for f in run.findings if f.decision == "adopted"),
    )


def predict_for(change_request: ChangeRequest, baseline: Baseline) -> Prediction:
    """CR の対象から、基準の束(写し付き)の要素で影響を予測する。"""
    return predict_from_snapshots(
        baseline_snapshots(baseline),
        targets_from_json(change_request.targets),
        adds_requirement=change_request.adds_requirement,
        origins=origin_keys_of(change_request),
    )


def facts_of(change_request: ChangeRequest, baseline_number: int) -> ChangeRequestFacts:
    """説明書の1節・2節に載る CR の値。"""
    return ChangeRequestFacts(
        number=change_request.number,
        title=change_request.title,
        status=change_request.status,  # type: ignore[arg-type]
        content=change_request.content,
        background_source=change_request.background_source,  # type: ignore[arg-type]
        background_date=change_request.background_date,
        background_memo=change_request.background_memo,
        targets=tuple(targets_from_json(change_request.targets)),
        adds_requirement=change_request.adds_requirement,
        baseline_number=baseline_number,
        origin_labels=tuple(str(o.get("label", "")) for o in change_request.origins or []),
        options=options_from_json(change_request.options),
        decision_option=change_request.decision_option,
    )


def baseline_requirement_ids(baseline: Baseline) -> list[str]:
    """束の要件定義書の写しにある要件 ID(行の順)。写しが無い・model が無ければ空。"""
    return [str(row["id"]) for row in baseline_requirement_rows(baseline)]


def origin_keys_of(change_request: ChangeRequest) -> list[str]:
    """CR に保存した新しい要件の予測の起点のキー(選んだ順)。"""
    return [str(o["key"]) for o in change_request.origins or []]


def checked_origins(
    keys: Sequence[str], adds_requirement: bool, baseline: Baseline
) -> list[dict[str, str]] | None:
    """新しい要件の予測の起点(要素のキー)を確かめ、保存の形(`[{key, label}]`。選んだ順・重なりを
    除く)にする。起点が無ければ None。新しい要件を足さない CR の起点と、基準の束の要件・画面・API・
    段階1 の処理に無いキーは ChangeRequestTargetUnknownError。表示名は束の写しから写す(束は
    凍結されている)。"""
    wanted = list(dict.fromkeys(key.strip() for key in keys if key.strip()))
    if not wanted:
        return None
    if not adds_requirement:
        raise ChangeRequestTargetUnknownError(
            "予測の起点は、新しい要件を足す CR だけが持てます。"
        )
    candidates = {
        c.key: c for c in origin_candidates_from_snapshots(baseline_snapshots(baseline))
    }
    unknown = [key for key in wanted if key not in candidates]
    if unknown:
        raise ChangeRequestTargetUnknownError(
            f"基準の合意済みの版(B-{baseline.number})に無い要件・画面・API・処理です: "
            f"{', '.join(unknown)}"
        )
    return [{"key": key, "label": candidates[key].label} for key in wanted]


def checked_options(
    options: ChangeOptions, targets: Sequence[Target], adds_requirement: bool
) -> ChangeOptions:
    """案(整えたもの。スキーマが規則を確かめる)の対象が CR の対象の中にあるかを確かめる。
    合わなければ ChangeRequestOptionInvalidError。"""
    problems = option_target_problems(options, targets, adds_requirement)
    if problems:
        raise ChangeRequestOptionInvalidError(" / ".join(problems))
    return options


def chosen_option(change_request: ChangeRequest, payload: ChangeRequestDecision) -> int | None:
    """合意で選んだ案の番号(案の無い CR・却下は None)。案のある CR の合意で選ばない・番号の案が
    無い・案の無い CR や却下で選んだときは ChangeRequestOptionInvalidError。"""
    options = options_from_json(change_request.options)
    if payload.decision != "agreed" or options.empty:
        if payload.option is not None:
            raise ChangeRequestOptionInvalidError(
                "案を選べるのは、案のある CR の合意だけです。"
            )
        return None
    if payload.option is None:
        raise ChangeRequestOptionInvalidError("合意する案を選んでください。")
    if payload.option > len(options.items):
        raise ChangeRequestOptionInvalidError(f"{option_label(payload.option)}はありません。")
    return payload.option


def chosen_option_name(change_request: ChangeRequest) -> str | None:
    """合意で選んだ案の表示名(`案2 メールだけ`。選んでいなければ None)。"""
    number = change_request.decision_option
    options = options_from_json(change_request.options)
    if number is None or not 1 <= number <= len(options.items):
        return None
    return f"{option_label(number)} {options.option(number).name}"


def checked_targets(
    targets: Sequence[Target], adds_requirement: bool, baseline: Baseline
) -> list[Target]:
    """対象(整えたもの。スキーマが空白・重複を除く)の要件 ID が、基準の束の要件定義書にあるかを
    確かめる。無い ID があるか、対象が空で新しい要件を足す印も無ければ
    ChangeRequestTargetUnknownError。"""
    known = set(baseline_requirement_ids(baseline))
    unknown = [req_id for req_id in target_ids(targets) if req_id not in known]
    if unknown:
        raise ChangeRequestTargetUnknownError(
            f"基準の合意済みの版(B-{baseline.number})の要件定義書に無い要件です: "
            f"{', '.join(unknown)}"
        )
    if not targets and not adds_requirement:
        raise ChangeRequestTargetUnknownError(
            "対象の要件を選ぶか、新しい要件を足す CR にしてください。"
        )
    return list(targets)
