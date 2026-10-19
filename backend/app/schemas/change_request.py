import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.change_requests import (
    BACKGROUND_SOURCE_LABEL,
    COMPARISON_STATUS_LABEL,
    COMPARISON_STATUSES,
    PREDICTED_ONLY_REASON_LABEL,
    STATUS_LABEL,
    SUMMARY_MAX,
    SUMMARY_STATE_LABEL,
    TARGET_KIND_LABEL,
    BackgroundSource,
    ChangeRequestAction,
    ChangeRequestStatus,
    Comparison,
    ComparisonStatus,
    Explanation,
    Overlap,
    PredictedOnlyReason,
    Prediction,
    SummaryState,
    Target,
    TargetKind,
    check_summary,
    explanation_filename,
    normalize_targets,
    targets_from_json,
)
from app.schemas.agreement import MEMO_MAX, REASON_MAX, AgreementNotes

if TYPE_CHECKING:
    from app.models.change_request import ChangeRequest
    from app.models.change_request_summary import ChangeRequestSummary

# CR の文の上限(タイトルは一覧の1行、内容は変更の申し出の本文)
TITLE_MAX = 200
CONTENT_MAX = 20000
# 1つの CR の対象の要件の数の上限
TARGETS_MAX = 100
# 対象1つの変更後の案の上限(要件1行の文)
PROPOSED_TEXT_MAX = 2000

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX)]
Content = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CONTENT_MAX)
]


class ChangeRequestTargetIn(BaseModel):
    """CR の対象1つ(要件 ID・変更の種類・変更後の案。種類を省くと内容の変更、案は任意で、
    削除の対象には書けない)。"""

    requirement_id: str
    kind: TargetKind = "content"
    proposed_text: str = Field(default="", max_length=PROPOSED_TEXT_MAX)


TargetsIn = Annotated[list[ChangeRequestTargetIn], Field(max_length=TARGETS_MAX)]


def _normalized(value: list[ChangeRequestTargetIn] | None) -> list[ChangeRequestTargetIn] | None:
    """対象を整える(空白・同じ要件の重複を除く)。同じ要件が違う種類・違う案のときと、削除の
    対象に案があるときは検証の誤り(422)。"""
    if value is None:
        return None
    targets = normalize_targets(
        Target(t.requirement_id, t.kind, t.proposed_text) for t in value
    )
    return [
        ChangeRequestTargetIn(
            requirement_id=t.requirement_id, kind=t.kind, proposed_text=t.proposed_text
        )
        for t in targets
    ]


class ChangeRequestCreate(BaseModel):
    """CR を作るときの本文。背景は出どころの種類・日付(日の粒度。任意)・メモ。対象は基準の束の
    要件定義書にある要件 ID と変更の種類の組と、新しい要件を足すかの印(どちらか一方は要る。
    サービスで確かめる)。
    基準の束の番号を省くと、最新の束を基準にする。"""

    title: Title
    content: Content
    background_source: BackgroundSource
    background_date: date | None = None
    background_memo: str = Field(default="", max_length=MEMO_MAX)
    targets: TargetsIn = Field(default_factory=list)
    adds_requirement: bool = False
    baseline_number: int | None = Field(default=None, ge=1)

    @field_validator("targets")
    @classmethod
    def _normalize_targets(cls, value: list[ChangeRequestTargetIn]) -> list[ChangeRequestTargetIn]:
        return _normalized(value) or []


class ChangeRequestUpdate(BaseModel):
    """CR の中身の更新(送った項目だけを変える)。基準の束は変えない。`background_date`は null で
    消せる。"""

    title: Title | None = None
    content: Content | None = None
    background_source: BackgroundSource | None = None
    background_date: date | None = None
    background_memo: str | None = Field(default=None, max_length=MEMO_MAX)
    targets: TargetsIn | None = None
    adds_requirement: bool | None = None

    @field_validator("targets")
    @classmethod
    def _normalize_targets(
        cls, value: list[ChangeRequestTargetIn] | None
    ) -> list[ChangeRequestTargetIn] | None:
        return _normalized(value)


class ChangeRequestDecision(AgreementNotes):
    """CR の合意・却下の記録。相手・メモ・理由は合意済みの版と同じ(AgreementNotes)。判断の日時は
    記録する日時と別に、タイムゾーン付きで受け取る。"""

    decision: Literal["agreed", "rejected"]
    decided_at: AwareDatetime


class ChangeRequestWithdraw(BaseModel):
    """CR の取り下げ。理由は任意(時系列に残す)。"""

    reason: str = Field(default="", max_length=REASON_MAX)


class ChangeRequestEventRead(BaseModel):
    """CR の時系列の1行。`from_status`は作成・写しで null。`refs`は行が指すもの(反映: 作った文書の
    版とモデル名、段階の反映: 段階・版・モデル名)。指すものの無い行は null。"""

    model_config = ConfigDict(from_attributes=True)

    action: str
    from_status: str | None
    to_status: str
    occurred_at: datetime
    actor_id: uuid.UUID | None
    note: str
    refs: dict[str, Any] | None = None


class ChangeRequestDecisionRead(BaseModel):
    """CR の合意・却下の記録(合意済みの版と同じ項目)。"""

    counterpart: str | None
    decided_at: datetime
    memo: str
    reason: str
    recorded_by: uuid.UUID | None


class ChangeRequestTargetRead(BaseModel):
    """CR の対象1つ(要件 ID・変更の種類と、その表示名)。"""

    requirement_id: str
    kind: TargetKind
    kind_label: str


class ChangeRequestTargetDetailRead(ChangeRequestTargetRead):
    """CR の対象1つ(一覧・詳細用。変更後の案つき)。"""

    proposed_text: str

    @classmethod
    def from_target(cls, target: Target) -> "ChangeRequestTargetDetailRead":
        return cls(
            requirement_id=target.requirement_id,
            kind=target.kind,
            kind_label=TARGET_KIND_LABEL[target.kind],
            proposed_text=target.proposed_text,
        )


class ChangeRequestOverlapRead(BaseModel):
    """同じ要件を対象にする、ほかの進行中の CR。"""

    number: int
    label: str
    requirement_ids: list[str]


class ChangeRequestSummaryRead(BaseModel):
    """CR 1つ(一覧用)。`label`は画面に出す名前(CR-1…)、`baseline_label`は基準の束(B-1…)。"""

    number: int
    label: str
    title: str
    status: ChangeRequestStatus
    status_label: str
    background_source: BackgroundSource
    background_source_label: str
    background_date: date | None
    targets: list[ChangeRequestTargetDetailRead]
    adds_requirement: bool
    baseline_number: int
    baseline_label: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, change_request: "ChangeRequest") -> "ChangeRequestSummaryRead":
        return cls.model_validate(summary_fields(change_request))


class ChangeRequestRead(ChangeRequestSummaryRead):
    """CR 1つ(詳細)。中身・合意の記録・時系列・重なり・写しの元と先・今できる操作。"""

    content: str
    background_memo: str
    decision: ChangeRequestDecisionRead | None
    events: list[ChangeRequestEventRead]
    overlapping: list[ChangeRequestOverlapRead]
    derived_from_number: int | None
    derived_numbers: list[int]
    allowed_actions: list[ChangeRequestAction]

    @classmethod
    def build(
        cls,
        change_request: "ChangeRequest",
        *,
        overlapping: list[Overlap],
        derived_from_number: int | None,
        derived_numbers: list[int],
        allowed_actions: list[ChangeRequestAction],
    ) -> "ChangeRequestRead":
        decision = None
        if change_request.decided_at is not None:
            decision = ChangeRequestDecisionRead(
                counterpart=change_request.decision_counterpart,
                decided_at=change_request.decided_at,
                memo=change_request.decision_memo or "",
                reason=change_request.decision_reason or "",
                recorded_by=change_request.decision_recorded_by,
            )
        return cls.model_validate(
            {
                **summary_fields(change_request),
                "content": change_request.content,
                "background_memo": change_request.background_memo,
                "decision": decision,
                "events": [
                    ChangeRequestEventRead.model_validate(event)
                    for event in change_request.events
                ],
                "overlapping": [
                    ChangeRequestOverlapRead(
                        number=o.number,
                        label=change_request_label(o.number),
                        requirement_ids=o.requirement_ids,
                    )
                    for o in overlapping
                ],
                "derived_from_number": derived_from_number,
                "derived_numbers": derived_numbers,
                "allowed_actions": allowed_actions,
            }
        )


class PredictedTargetRead(ChangeRequestTargetRead):
    """予測の起点の要件1つ(基準の束の要件定義書の文つき)。"""

    text: str


class PredictedItemRead(BaseModel):
    """見直しが要る要素1つ。`path`は起点の要件からの道、`requirement_ids`は届いた対象の要件。"""

    key: str
    kind: str
    stage: int
    label: str
    path: list[str]
    reason: str
    requirement_ids: list[str]


class ComparedItemRead(BaseModel):
    """予測と実際の印の比較の要素1つ。`status`は一致 / 予測だけ / 実際だけ、
    `predicted_only_reason`は予測だけのときの理由(文書の側・承認していない段階・差分が届かな
    かった)。`reviewed`は反映のときの印の確認の状態(印が無ければ null)。"""

    key: str
    label: str
    stage: int
    status: ComparisonStatus
    status_label: str
    predicted_only_reason: PredictedOnlyReason | None
    predicted_only_reason_label: str | None
    predicted_reason: str
    actual_reason: str
    requirement_ids: list[str]
    reviewed: bool | None


class ChangeRequestComparisonRead(BaseModel):
    """反映した CR の、影響の予測と反映で付いた印の比較。`counts`は分類ごとの件数、`saved`は予測が
    判断の時点で保存したものか。"""

    number: int
    label: str
    status: ChangeRequestStatus
    saved: bool
    counts: dict[ComparisonStatus, int]
    items: list[ComparedItemRead]

    @classmethod
    def build(
        cls, change_request: "ChangeRequest", comparison: Comparison, *, saved: bool
    ) -> "ChangeRequestComparisonRead":
        return cls(
            number=change_request.number,
            label=change_request_label(change_request.number),
            status=change_request.status,  # type: ignore[arg-type]
            saved=saved,
            counts={status: comparison.count(status) for status in COMPARISON_STATUSES},
            items=[
                ComparedItemRead(
                    key=i.key,
                    label=i.label,
                    stage=i.stage,
                    status=i.status,
                    status_label=COMPARISON_STATUS_LABEL[i.status],
                    predicted_only_reason=i.predicted_only_reason,
                    predicted_only_reason_label=(
                        PREDICTED_ONLY_REASON_LABEL[i.predicted_only_reason]
                        if i.predicted_only_reason is not None
                        else None
                    ),
                    predicted_reason=i.predicted_reason,
                    actual_reason=i.actual_reason,
                    requirement_ids=list(i.requirement_ids),
                    reviewed=i.reviewed,
                )
                for i in comparison.items
            ],
        )


class ExcludedItemRead(BaseModel):
    """基準の束に入っていない文書・段階(その要素は予測に出ない)。`state`は束を作ったときの状態。"""

    key: str
    state: str


class ChangeRequestImpactRead(BaseModel):
    """CR の影響の予測。`saved`は判断の時点で保存したものか(偽なら今、基準の束から求めたもの)。
    `unpredicted_new_requirement`は、新しい要件を足す分が予測に入っていないこと(起点が無いため)。"""

    number: int
    label: str
    baseline_number: int
    baseline_label: str
    saved: bool
    targets: list[PredictedTargetRead]
    items: list[PredictedItemRead]
    unpredicted_new_requirement: bool
    excluded: list[ExcludedItemRead]

    @classmethod
    def build(
        cls,
        change_request: "ChangeRequest",
        prediction: Prediction,
        *,
        saved: bool,
        excluded: list[ExcludedItemRead],
    ) -> "ChangeRequestImpactRead":
        baseline_number = change_request.baseline.number
        return cls(
            number=change_request.number,
            label=change_request_label(change_request.number),
            baseline_number=baseline_number,
            baseline_label=f"B-{baseline_number}",
            saved=saved,
            targets=[
                PredictedTargetRead(
                    requirement_id=t.requirement_id,
                    kind=t.kind,
                    kind_label=TARGET_KIND_LABEL[t.kind],
                    text=t.text,
                )
                for t in prediction.targets
            ],
            items=[
                PredictedItemRead(
                    key=i.key,
                    kind=i.kind,
                    stage=i.stage,
                    label=i.label,
                    path=list(i.path),
                    reason=i.reason,
                    requirement_ids=list(i.requirement_ids),
                )
                for i in prediction.items
            ],
            unpredicted_new_requirement=prediction.unpredicted_new_requirement,
            excluded=excluded,
        )


class SummaryEdit(BaseModel):
    """説明書の要約を直す本文。"""

    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SUMMARY_MAX)
    ]


class ExplanationSummaryRead(BaseModel):
    """説明書の要約。`state`は今の5節と比べた状態(古い = 作った・確かめた後に5節が変わった)。
    `generated_text`は LLM の元の文、`text`は今の文。照合は、`text`に出る要件 ID・数字のうち
    5節に無いもの。"""

    text: str
    generated_text: str
    edited: bool
    state: SummaryState
    state_label: str
    generated_model: str | None
    generated_by: uuid.UUID | None
    generated_at: datetime
    edited_at: datetime | None
    confirmed_by: uuid.UUID | None
    confirmed_at: datetime | None
    unknown_requirement_ids: list[str]
    unknown_numbers: list[str]

    @classmethod
    def build(
        cls, summary: "ChangeRequestSummary", state: SummaryState, explanation: Explanation
    ) -> "ExplanationSummaryRead":
        check = check_summary(summary.text, explanation)
        return cls(
            text=summary.text,
            generated_text=summary.generated_text,
            edited=summary.text != summary.generated_text,
            state=state,
            state_label=SUMMARY_STATE_LABEL[state],
            generated_model=summary.generated_model,
            generated_by=summary.generated_by,
            generated_at=summary.generated_at,
            edited_at=summary.edited_at,
            confirmed_by=summary.confirmed_by,
            confirmed_at=summary.confirmed_at,
            unknown_requirement_ids=list(check.unknown_requirement_ids),
            unknown_numbers=list(check.unknown_numbers),
        )


class ChangeRequestExplanationRead(BaseModel):
    """CR の説明書(プレビュー)。`markdown`は5節、`fingerprint`はその sha256、`output`は出力する
    md(確認済みで古くない要約だけを載せる)。作業単位とテストは5節の数の内訳(`has_test_spec`が
    偽なら、束にテスト仕様書が無くテストは数えていない)。`summary`は要約(作っていなければ null)。"""

    number: int
    label: str
    filename: str
    markdown: str
    fingerprint: str
    output: str
    units: list[str]
    integration_tests: list[str]
    system_tests: list[str]
    has_test_spec: bool
    summary: ExplanationSummaryRead | None

    @classmethod
    def build(
        cls,
        change_request: "ChangeRequest",
        explanation: Explanation,
        *,
        output: str,
        summary: "ChangeRequestSummary | None",
        summary_state: SummaryState | None,
    ) -> "ChangeRequestExplanationRead":
        units = explanation.units
        return cls(
            number=change_request.number,
            label=change_request_label(change_request.number),
            filename=explanation_filename(change_request.number),
            markdown=explanation.markdown,
            fingerprint=explanation.fingerprint,
            output=output,
            units=list(units.units),
            integration_tests=list(units.integration_tests),
            system_tests=list(units.system_tests),
            has_test_spec=units.has_test_spec,
            summary=(
                ExplanationSummaryRead.build(summary, summary_state, explanation)
                if summary is not None and summary_state is not None
                else None
            ),
        )


def change_request_label(number: int) -> str:
    return f"CR-{number}"


def summary_fields(change_request: "ChangeRequest") -> dict[str, Any]:
    """一覧と詳細に共通の項目(基準の束は読み込み済みであること)。"""
    status: ChangeRequestStatus = change_request.status  # type: ignore[assignment]
    source: BackgroundSource = change_request.background_source  # type: ignore[assignment]
    baseline_number = change_request.baseline.number
    return {
        "number": change_request.number,
        "label": change_request_label(change_request.number),
        "title": change_request.title,
        "status": status,
        "status_label": STATUS_LABEL[status],
        "background_source": source,
        "background_source_label": BACKGROUND_SOURCE_LABEL[source],
        "background_date": change_request.background_date,
        "targets": [
            ChangeRequestTargetDetailRead.from_target(t)
            for t in targets_from_json(change_request.targets)
        ],
        "adds_requirement": change_request.adds_requirement,
        "baseline_number": baseline_number,
        "baseline_label": f"B-{baseline_number}",
        "created_by": change_request.created_by,
        "created_at": change_request.created_at,
        "updated_at": change_request.updated_at,
    }
