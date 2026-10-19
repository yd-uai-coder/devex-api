import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.catalog import Purpose
from app.ai.llm.content import extract_text_content
from app.change_requests import (
    ACTIVE_STATUSES,
    COPYABLE_STATUSES,
    DECISION_ACTIONS,
    DOCUMENT_REFLECTABLE_STATUSES,
    INITIAL_STATUS,
    STAGE_REFLECTABLE_STATUSES,
    ChangeRequestAction,
    ChangeRequestFacts,
    ChangeRequestStatus,
    Comparison,
    Explanation,
    Overlap,
    Prediction,
    SummaryState,
    Target,
    allowed_actions,
    applied_marks_from_json,
    build_cr_instruction,
    build_explanation,
    build_summary_messages,
    clip_summary,
    compare,
    next_status,
    overlaps,
    predict_from_snapshots,
    summary_state,
    target_ids,
    targets_from_json,
    targets_to_json,
    with_summary,
)
from app.change_requests.explanation import DOCUMENT_LABEL
from app.detailed_design.impact import Mark
from app.models.baseline import Baseline
from app.models.change_request import ChangeRequest
from app.models.change_request_summary import ChangeRequestSummary
from app.models.project import Project
from app.repositories.baseline import BaselineRepository
from app.repositories.change_request import ChangeRequestRepository
from app.repositories.change_request_summary import ChangeRequestSummaryRepository
from app.repositories.design_stage import DesignStageRepository
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestUpdate,
    ChangeRequestWithdraw,
    change_request_label,
)
from app.services.baseline_service import BaselineService
from app.services.errors import (
    ChangeRequestBaselineMissingError,
    ChangeRequestInvalidTransitionError,
    ChangeRequestNotAppliedError,
    ChangeRequestNotFoundError,
    ChangeRequestNotReflectableError,
    ChangeRequestSummaryNotFoundError,
    ChangeRequestTargetUnknownError,
    DesignStagesNotAvailableError,
)
from app.services.llm_resolver import LlmResolver, ResolvedLlm, injected_llm
from app.services.llm_retry import invoke_with_retry

logger = structlog.get_logger(__name__)

# 対象の要件を読む、束の写しの key
REQUIREMENTS_KEY = "requirements"
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


@dataclass(frozen=True)
class ComparisonView:
    """CR 1つの、影響の予測と反映で付いた印の比較。`saved`は予測が判断の時点で保存したものか。"""

    change_request: ChangeRequest
    comparison: Comparison
    saved: bool


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
    CR・束の写し・予測から決定的に組み立てる(app/change_requests/explanation.py)。
    詳細設計モードだけ。"""

    def __init__(self, session: AsyncSession, *, llm: Any = None) -> None:
        # llm: テストが差し替える要約の LLM(None なら利用者の生成の用途で解決する)
        self._session = session
        self._llm = llm
        self._change_requests = ChangeRequestRepository(session)
        self._baselines = BaselineRepository(session)
        self._summaries = ChangeRequestSummaryRepository(session)

    async def create(
        self, project: Project, payload: ChangeRequestCreate, *, created_by: uuid.UUID | None
    ) -> ChangeRequestView:
        """CR を下書きで作る。基準の束は番号の束(省けば最新の束)。束が1つも無ければ
        ChangeRequestBaselineMissingError、番号の束が無ければ BaselineNotFoundError。"""
        if project.mode != "detailed":
            raise DesignStagesNotAvailableError(
                f"Change requests are not available for project {project.id} "
                f"(mode={project.mode})"
            )
        baseline = await self._base_baseline(project, payload.baseline_number)
        targets = checked_targets(
            [Target(t.requirement_id, t.kind, t.proposed_text) for t in payload.targets],
            payload.adds_requirement,
            baseline,
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
        """プロジェクトの CR を新しい順に返す。"""
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
        戻る(時系列に残す)。対象を変えたら、基準の束の要件定義書で確かめ直す。"""
        change_request = await self._load(project, number)
        to_status = self._next(change_request, "edit")
        changes = payload.model_dump(exclude_unset=True)
        if "targets" in changes or "adds_requirement" in changes:
            adds = changes.get("adds_requirement")
            targets = (
                targets_from_json(change_request.targets)
                if payload.targets is None
                else [Target(t.requirement_id, t.kind, t.proposed_text) for t in payload.targets]
            )
            changes["targets"] = targets_to_json(
                checked_targets(
                    targets,
                    change_request.adds_requirement if adds is None else adds,
                    await self._baseline_with_items(change_request),
                )
            )
        for field, value in changes.items():
            # 必須の項目に null を送ったときは、変えない(背景の日付だけは null で消せる)
            if value is None and field != "background_date":
                continue
            setattr(change_request, field, value)
        if to_status != change_request.status:
            await self._change_requests.add_event(
                change_request, action="edit", to_status=to_status, actor_id=actor_id
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
        await self._change_requests.add_event(
            change_request,
            action="explain",
            to_status=self._next(change_request, "explain"),
            actor_id=actor_id,
        )
        await self._session.commit()
        return await self._view(change_request)

    async def decide(
        self,
        project: Project,
        number: int,
        payload: ChangeRequestDecision,
        *,
        actor_id: uuid.UUID | None,
    ) -> ChangeRequestView:
        """説明済みの CR を合意か却下にし、合意の記録(相手・日時・メモ・理由・記録した人)と、
        その時点の影響の予測を残す。"""
        change_request = await self._load(project, number)
        action = DECISION_ACTIONS[payload.decision]
        to_status = self._next(change_request, action)
        # 判断のときに示した影響の範囲を残す(後でたどり方の規則を直しても変わらないように)
        baseline = await self._baseline_with_items(change_request)
        change_request.predicted_impact = predict_for(change_request, baseline).to_dict()
        change_request.decision_counterpart = payload.counterpart
        change_request.decided_at = payload.decided_at
        change_request.decision_memo = payload.memo
        change_request.decision_reason = payload.reason
        change_request.decision_recorded_by = actor_id
        await self._change_requests.add_event(
            change_request, action=action, to_status=to_status, actor_id=actor_id
        )
        await self._session.commit()
        logger.info(
            "change_request_decided",
            project_id=str(project.id),
            number=number,
            decision=payload.decision,
        )
        return await self._view(change_request)

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
        """却下した CR を写して、新しい番号の CR を下書きで作る(中身・背景・対象・基準の束)。
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

    async def comparison(self, project: Project, number: int) -> ComparisonView:
        """反映した CR の、影響の予測(判断の時点で保存したもの)と反映で付いた印の比較。反映の前は
        ChangeRequestNotAppliedError。印を付ける段階は、今、一度でも承認した段階。"""
        change_request = await self._load(project, number)
        if change_request.status not in APPLIED_STATUSES:
            raise ChangeRequestNotAppliedError(
                f"{change_request_label(number)} はまだ反映していません。"
            )
        _, view = await self._impact_with_baseline(change_request)
        rows = await DesignStageRepository(self._session).list_for_project(project.id)
        marked = {row.stage for row in rows if row.approved_version is not None}
        comparison = compare(
            view.prediction, applied_marks_from_json(change_request.applied_impact), marked
        )
        return ComparisonView(change_request, comparison, saved=view.saved)

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
        )

    async def _base_baseline(self, project: Project, number: int | None) -> Baseline:
        if number is not None:
            return await BaselineService(self._session).get(project, number)
        latest = await self._baselines.list_for_project(project.id)
        if not latest:
            raise ChangeRequestBaselineMissingError(f"No baseline in project {project.id}")
        return latest[0]

    async def _baseline_with_items(self, change_request: ChangeRequest) -> Baseline:
        """CR の基準の束を、写し付きで読む(CR と一緒に読んだ束は写しを読んでいないため)。"""
        baseline = await self._baselines.find_one(id=change_request.baseline_id)
        assert baseline is not None  # 束は消えない(CR はプロジェクトの削除でだけ一緒に消える)
        return baseline

    async def _load(self, project: Project, number: int) -> ChangeRequest:
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


def merge_applied_marks(
    existing: Sequence[Mapping[str, Any]] | None, marks: Sequence[Mark]
) -> list[dict[str, Any]]:
    """反映で付いた印の写しに、新しい印を重ねる(同じ要素は新しい印にする。置き場とキーの順)。"""
    by_key = {str(item["key"]): dict(item) for item in existing or []}
    for mark in marks:
        by_key[mark.key] = asdict(mark)
    return sorted(by_key.values(), key=lambda m: (int(m["stage"]), str(m["key"])))


def predict_for(change_request: ChangeRequest, baseline: Baseline) -> Prediction:
    """CR の対象から、基準の束(写し付き)の要素で影響を予測する。"""
    snapshots = {item.key: item.model for item in baseline.items if item.included}
    return predict_from_snapshots(
        snapshots,
        targets_from_json(change_request.targets),
        adds_requirement=change_request.adds_requirement,
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
    )


def baseline_requirement_ids(baseline: Baseline) -> list[str]:
    """束の要件定義書の写しにある要件 ID(行の順)。写しが無い・model が無ければ空。"""
    item = next((i for i in baseline.items if i.key == REQUIREMENTS_KEY), None)
    if item is None or not item.model:
        return []
    return [str(row["id"]) for row in item.model.get("rows", []) if row.get("id")]


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
