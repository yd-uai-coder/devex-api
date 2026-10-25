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
    model_validator,
)

from app.change_requests import (
    BACKGROUND_SOURCE_LABEL,
    COMPARISON_STATUS_LABEL,
    COMPARISON_STATUSES,
    MARK_CONFIRMATION_LABEL,
    OPTION_DRAFT_STATUS_LABEL,
    OPTION_NAME_MAX,
    OPTION_OUTCOME_MAX,
    OPTIONS_MAX,
    PREDICTED_ONLY_REASON_LABEL,
    RECOMMEND_REASON_MAX,
    SNAPSHOT_MARKDOWN_KEY,
    STATUS_LABEL,
    SUMMARY_MAX,
    SUMMARY_STATE_LABEL,
    TARGET_KIND_LABEL,
    BackgroundSource,
    ChangeOptions,
    ChangeRequestAction,
    ChangeRequestStatus,
    Comparison,
    ComparisonStatus,
    Explanation,
    MarkConfirmation,
    OptionDraftCheck,
    OptionDraftStatus,
    OptionScale,
    Overlap,
    PredictedOnlyReason,
    Prediction,
    Scale,
    ScaleComparisonRow,
    ScaleCount,
    ScaleKind,
    ScaleRemark,
    SummaryState,
    Target,
    TargetKind,
    TraceEntry,
    Verification,
    check_summary,
    decision_source_label,
    explanation_filename,
    needs_reconfirm,
    normalize_options,
    normalize_targets,
    option_label,
    options_from_json,
    options_from_rows,
    targets_from_json,
)
from app.schemas.agreement import MEMO_MAX, REASON_MAX, AgreementNotes

if TYPE_CHECKING:
    from app.models.change_request import ChangeRequest, ChangeRequestReview
    from app.models.change_request_option_draft import ChangeRequestOptionDraft
    from app.models.change_request_summary import ChangeRequestSummary
    from app.models.review import ReviewRun

# CR の文の上限(タイトルは一覧の1行、内容は変更の申し出の本文)
TITLE_MAX = 200
CONTENT_MAX = 20000
# 1つの CR の対象の要件の数の上限
TARGETS_MAX = 100
# 新しい要件の予測の起点の数の上限
ORIGINS_MAX = 20
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


OriginsIn = Annotated[list[str], Field(max_length=ORIGINS_MAX)]


class ChangeRequestOptionIn(BaseModel):
    """案1つ(名前・この案で変える対象・結果)。対象は CR の対象の中だけ(サービスで確かめる)。"""

    name: str = Field(max_length=OPTION_NAME_MAX)
    targets: TargetsIn = Field(default_factory=list)
    outcome: str = Field(max_length=OPTION_OUTCOME_MAX)


class ChangeRequestOptionsIn(BaseModel):
    """CR の選択肢(案は0件か2〜3件)と、おすすめの案の番号(1始まり)と理由。案を消すときは
    `items`を空で送る。"""

    items: Annotated[list[ChangeRequestOptionIn], Field(max_length=OPTIONS_MAX)] = Field(
        default_factory=list
    )
    recommended: int | None = Field(default=None, ge=1)
    reason: str = Field(default="", max_length=RECOMMEND_REASON_MAX)

    def to_options(self) -> ChangeOptions:
        """整えた案(normalize_options。規則に合わなければ ValueError)。"""
        return normalize_options(
            options_from_rows(
                (
                    (
                        o.name,
                        (Target(t.requirement_id, t.kind, t.proposed_text) for t in o.targets),
                        o.outcome,
                    )
                    for o in self.items
                ),
                self.recommended,
                self.reason,
            )
        )

    @model_validator(mode="after")
    def _check(self) -> "ChangeRequestOptionsIn":
        # 案の数・名前・おすすめ・対象の規則(合わなければ検証の誤り 422)
        self.to_options()
        return self


class ChangeRequestCreate(BaseModel):
    """CR を作るときの本文。背景は出どころの種類・日付(日の粒度。任意)・メモ。対象は基準の束の
    要件定義書にある要件 ID と変更の種類の組と、新しい要件を足すかの印(どちらか一方は要る。
    サービスで確かめる)。`origins`は新しい要件の予測の起点(基準の束の要件・画面・API・処理の要素の
    キー。新しい要件を足す CR だけが持てる。サービスで確かめる)。
    基準の束の番号を省くと、最新の束を基準にする。"""

    title: Title
    content: Content
    background_source: BackgroundSource
    background_date: date | None = None
    background_memo: str = Field(default="", max_length=MEMO_MAX)
    targets: TargetsIn = Field(default_factory=list)
    adds_requirement: bool = False
    origins: OriginsIn = Field(default_factory=list)
    options: ChangeRequestOptionsIn | None = None
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
    origins: OriginsIn | None = None
    options: ChangeRequestOptionsIn | None = None

    @field_validator("targets")
    @classmethod
    def _normalize_targets(
        cls, value: list[ChangeRequestTargetIn] | None
    ) -> list[ChangeRequestTargetIn] | None:
        return _normalized(value)


class ChangeRequestDecision(AgreementNotes):
    """CR の合意・却下の記録。相手・メモ・理由は合意済みの版と同じ(AgreementNotes)。判断の日時は
    記録する日時と別に、タイムゾーン付きで受け取る。`option`は合意で選んだ案の番号(1始まり。
    案のある CR の合意では必須。サービスで確かめる)。"""

    decision: Literal["agreed", "rejected"]
    decided_at: AwareDatetime
    option: int | None = Field(default=None, ge=1)


class ChangeRequestWithdraw(BaseModel):
    """CR の取り下げ。理由は任意(時系列に残す)。"""

    reason: str = Field(default="", max_length=REASON_MAX)


class ChangeRequestEventRead(BaseModel):
    """CR の時系列の1行。`from_status`は作成・写しで null。`refs`は行が指すもの(反映: 作った文書の
    版とモデル名、段階の反映: 段階・版・モデル名、レビューを結ぶ: 回・区切り・モデル名、検証:
    確かめた数と結んだレビュー、束に入れる: 束の番号、説明: その時点の説明書の指紋)。指すものの
    無い行は null。説明の行に残した説明書の md の写しは、応答に出さない(共有リンクの「前回から
    変わった点」だけが読む)。"""

    model_config = ConfigDict(from_attributes=True)

    action: str
    from_status: str | None
    to_status: str
    occurred_at: datetime
    actor_id: uuid.UUID | None
    note: str
    refs: dict[str, Any] | None = None

    @field_validator("refs")
    @classmethod
    def _drop_snapshot(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None or SNAPSHOT_MARKDOWN_KEY not in value:
            return value
        return {k: v for k, v in value.items() if k != SNAPSHOT_MARKDOWN_KEY}


class ReviewRunBriefRead(BaseModel):
    """CR に結ぶ(結んだ)設計レビューの回の要点。`findings`は指摘の数、`adopted`は採用した数。"""

    review_run_id: uuid.UUID
    gate: str
    model: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    findings: int
    adopted: int

    @classmethod
    def from_run(cls, run: "ReviewRun") -> "ReviewRunBriefRead":
        return cls.model_validate(review_run_fields(run))


class LinkedReviewRead(ReviewRunBriefRead):
    """CR に結んだ設計レビューの回(結んだ日時と開発者つき)。"""

    linked_at: datetime
    linked_by: uuid.UUID | None

    @classmethod
    def from_link(cls, link: "ChangeRequestReview") -> "LinkedReviewRead":
        return cls.model_validate(
            {
                **review_run_fields(link.review_run),
                "linked_at": link.linked_at,
                "linked_by": link.linked_by,
            }
        )


class ChangeRequestReviewLink(BaseModel):
    """CR に設計レビューの回を結ぶ。"""

    review_run_id: uuid.UUID


class ChangeRequestDecisionRead(BaseModel):
    """CR の合意・却下の記録(合意済みの版と同じ項目)。`source`は出どころ(developer = 開発者が
    記録 / share_link = 共有リンク。記録を足す前の判断は null)、`fingerprint`は判断したときの
    説明書の版。"""

    counterpart: str | None
    decided_at: datetime
    memo: str
    reason: str
    recorded_by: uuid.UUID | None
    option: int | None = None
    source: str | None = None
    source_label: str | None = None
    fingerprint: str | None = None


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


class ChangeRequestOriginRead(BaseModel):
    """新しい要件の予測の起点1つ(要素のキーと表示名)。"""

    key: str
    label: str


class ChangeRequestOptionRead(BaseModel):
    """案1つ(番号・呼び名・名前・対象・結果・おすすめか)。"""

    number: int
    label: str
    name: str
    targets: list[ChangeRequestTargetDetailRead]
    outcome: str
    recommended: bool


class ChangeRequestOptionsRead(BaseModel):
    """CR の選択肢(案の順)と、おすすめの理由(おすすめが無ければ空)。"""

    items: list[ChangeRequestOptionRead]
    recommended: int | None
    reason: str

    @classmethod
    def from_options(cls, options: ChangeOptions) -> "ChangeRequestOptionsRead":
        return cls(
            items=[
                ChangeRequestOptionRead(
                    number=number,
                    label=option_label(number),
                    name=option.name,
                    targets=[ChangeRequestTargetDetailRead.from_target(t) for t in option.targets],
                    outcome=option.outcome,
                    recommended=options.recommended == number,
                )
                for number, option in enumerate(options.items, start=1)
            ],
            recommended=options.recommended,
            reason=options.reason,
        )


class ChangeRequestOverlapRead(BaseModel):
    """同じ要件を対象にする、ほかの進行中の CR。"""

    number: int
    label: str
    requirement_ids: list[str]


class ChangeRequestSummaryRead(BaseModel):
    """CR 1つ(一覧用)。`label`は画面に出す名前(CR-1…)、`baseline_label`は基準の束(B-1…)。
    合意の記録と入れた束(検証済みの後)も持つ(束の作成で、入れる CR を選び、合意の記録の初期値に
    するため)。"""

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
    origins: list[ChangeRequestOriginRead]
    baseline_number: int
    baseline_label: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    decision: ChangeRequestDecisionRead | None
    included_baseline_number: int | None

    @classmethod
    def from_model(cls, change_request: "ChangeRequest") -> "ChangeRequestSummaryRead":
        return cls.model_validate(summary_fields(change_request))


class ChangeRequestRead(ChangeRequestSummaryRead):
    """CR 1つ(詳細)。中身・選択肢(案が無ければ null)・合意の記録・時系列・結んだレビュー・
    入れた束(検証済みの後)・重なり・写しの元と先・今できる操作。"""

    content: str
    background_memo: str
    options: ChangeRequestOptionsRead | None = None
    events: list[ChangeRequestEventRead]
    reviews: list[LinkedReviewRead]
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
        return cls.model_validate(
            {
                **summary_fields(change_request),
                "content": change_request.content,
                "background_memo": change_request.background_memo,
                "options": _options_read(change_request),
                "events": [
                    ChangeRequestEventRead.model_validate(event)
                    for event in change_request.events
                ],
                "reviews": [LinkedReviewRead.from_link(r) for r in change_request.reviews],
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
    """見直しが要る要素1つ。`path`は起点(対象の要件か新しい要件の起点)からの道、
    `requirement_ids`は届いた対象の要件、`origin_keys`は届いた新しい要件の起点。"""

    key: str
    kind: str
    stage: int
    label: str
    path: list[str]
    reason: str
    requirement_ids: list[str]
    origin_keys: list[str] = []


class ComparedItemRead(BaseModel):
    """予測と実際の印の比較の要素1つ。`status`は一致 / 予測だけ / 実際だけ、
    `predicted_only_reason`は予測だけのときの理由(文書の側・承認していない段階・差分が届かな
    かった)。`reviewed`は印の今の確認の状態(承認で消えた印は確認済み。文書の側は確認の一覧の
    状態。印が無く、文書の側でもなければ null)。"""

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


class ScaleComparisonRead(BaseModel):
    """規模の種類1つの、予測の数と実際の数(数えられなければ null)。`basis`は実際の数の出どころ
    (`marks` = 反映の印から今の設計で数えた、`document_checks` = 文書の側の確認の一覧で確かめた
    数)。"""

    kind: ScaleKind
    label: str
    predicted: int | None
    actual: int | None
    basis: Literal["marks", "document_checks"]

    @classmethod
    def build(cls, row: ScaleComparisonRow) -> "ScaleComparisonRead":
        return cls(
            kind=row.kind,
            label=row.label,
            predicted=row.predicted,
            actual=row.actual,
            basis=row.basis,
        )


class ChangeRequestComparisonRead(BaseModel):
    """反映した CR の、影響の予測と反映で付いた印の比較。`counts`は分類ごとの件数、`saved`は予測が
    判断の時点で保存したものか、`scale`は規模の種類ごとの予測の数と実際の数。"""

    number: int
    label: str
    status: ChangeRequestStatus
    saved: bool
    counts: dict[ComparisonStatus, int]
    items: list[ComparedItemRead]
    scale: list[ScaleComparisonRead] = []

    @classmethod
    def build(
        cls,
        change_request: "ChangeRequest",
        comparison: Comparison,
        *,
        saved: bool,
        scale: tuple[ScaleComparisonRow, ...] = (),
    ) -> "ChangeRequestComparisonRead":
        return cls(
            number=change_request.number,
            label=change_request_label(change_request.number),
            status=change_request.status,  # type: ignore[arg-type]
            saved=saved,
            scale=[ScaleComparisonRead.build(row) for row in scale],
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


class MarkStateRead(BaseModel):
    """反映で付いた印1つと、今の確認の状態(未確認 / 確認済み / 承認で済んだ)。"""

    key: str
    label: str
    stage: int
    reason: str
    confirmation: MarkConfirmation
    confirmation_label: str


class DocumentItemRead(BaseModel):
    """文書の側の確認の項目1つ(保存した予測の画面・API)。"""

    key: str
    label: str
    reason: str
    reviewed: bool


class ChangeRequestVerificationRead(BaseModel):
    """CR の検証の確かめ。`can_verify`は今、検証済みにできるか(反映済みで、印と文書の側に未確認が
    無く、CR で作り直した段階がすべて承認済み)。`open_marks`・`open_documents`は未確認の数、
    `pending_stages`は CR で作り直した後にまだ承認していない段階。"""

    number: int
    label: str
    status: ChangeRequestStatus
    can_verify: bool
    open_marks: int
    open_documents: int
    marks: list[MarkStateRead]
    documents: list[DocumentItemRead]
    pending_stages: list[int] = []

    @classmethod
    def build(
        cls, change_request: "ChangeRequest", verification: Verification, *, can_verify: bool
    ) -> "ChangeRequestVerificationRead":
        return cls(
            number=change_request.number,
            label=change_request_label(change_request.number),
            status=change_request.status,  # type: ignore[arg-type]
            can_verify=can_verify,
            open_marks=verification.open_marks,
            open_documents=verification.open_documents,
            marks=[
                MarkStateRead(
                    key=m.key,
                    label=m.label,
                    stage=m.stage,
                    reason=m.reason,
                    confirmation=m.confirmation,
                    confirmation_label=MARK_CONFIRMATION_LABEL[m.confirmation],
                )
                for m in verification.marks
            ],
            documents=[
                DocumentItemRead(key=d.key, label=d.label, reason=d.reason, reviewed=d.reviewed)
                for d in verification.documents
            ],
            pending_stages=list(verification.pending_stages),
        )


class DocumentChecksUpdate(BaseModel):
    """文書の側の確認の項目を、確認済み(または未確認)にする。予測に無いキーは無視する。"""

    keys: list[str] = Field(min_length=1, max_length=500)
    reviewed: bool = True


class ChangeRequestVerify(BaseModel):
    """CR を検証済みにする。`note`は任意の補足(時系列に残す)。"""

    note: str = Field(default="", max_length=REASON_MAX)


class TraceDocumentRead(BaseModel):
    """反映で作った文書の版1つ。`available`は今もその版が残っているか(直近の数版だけ残すため)。"""

    doc_type: str
    label: str
    version: int
    available: bool


class TraceStageRead(BaseModel):
    """反映で作り直した段階の下書きの版。"""

    stage: int
    version: int


class TraceReviewRead(BaseModel):
    """たどる記録の行が指す、結んだレビューの回の要点。"""

    review_run_id: str
    gate: str
    model: str
    status: str
    findings: int
    adopted: int


class TraceEntryRead(BaseModel):
    """たどる記録の1行。時系列の行と、指す先(作った文書の版・段階の版・モデル名・結んだ
    レビュー・入れた束)を読み解いたもの。"""

    position: int
    action: str
    action_label: str
    from_status: str | None
    to_status: str
    occurred_at: datetime
    actor_id: uuid.UUID | None
    note: str
    model: str | None
    documents: list[TraceDocumentRead]
    stage: TraceStageRead | None
    reviews: list[TraceReviewRead]
    baseline_number: int | None
    # 直した(edit)の行の、直した項目の名前(タイトル・対象…)。ほかの行と、項目を残す前の行は空
    edited_fields: list[str]
    # 段階の承認で印を結んだ行の、結んだ印の数。ほかの行は null
    mark_count: int | None = None

    @classmethod
    def from_entry(cls, entry: TraceEntry) -> "TraceEntryRead":
        return cls(
            position=entry.position,
            action=entry.action,
            action_label=entry.action_label,
            from_status=entry.from_status,
            to_status=entry.to_status,
            occurred_at=entry.occurred_at,
            actor_id=entry.actor_id,
            note=entry.note,
            model=entry.model,
            documents=[
                TraceDocumentRead(
                    doc_type=d.doc_type, label=d.label, version=d.version, available=d.available
                )
                for d in entry.documents
            ],
            stage=(
                TraceStageRead(stage=entry.stage.stage, version=entry.stage.version)
                if entry.stage is not None
                else None
            ),
            reviews=[
                TraceReviewRead(
                    review_run_id=r.review_run_id,
                    gate=r.gate,
                    model=r.model,
                    status=r.status,
                    findings=r.findings,
                    adopted=r.adopted,
                )
                for r in entry.reviews
            ],
            baseline_number=entry.baseline_number,
            edited_fields=list(entry.edited_fields),
            mark_count=entry.mark_count,
        )


class ChangeRequestTraceRead(BaseModel):
    """CR の、受付から検証までをたどる記録。基準の束(`baseline_number`)と、検証済みの後に入れた束
    (`included_baseline_number`。まだなら null)と、起きた順の行。"""

    number: int
    label: str
    status: ChangeRequestStatus
    baseline_number: int
    included_baseline_number: int | None
    entries: list[TraceEntryRead]

    @classmethod
    def build(
        cls, change_request: "ChangeRequest", entries: list[TraceEntry]
    ) -> "ChangeRequestTraceRead":
        included = change_request.included_baseline
        return cls(
            number=change_request.number,
            label=change_request_label(change_request.number),
            status=change_request.status,  # type: ignore[arg-type]
            baseline_number=change_request.baseline.number,
            included_baseline_number=included.number if included is not None else None,
            entries=[TraceEntryRead.from_entry(e) for e in entries],
        )


class ExcludedItemRead(BaseModel):
    """基準の束に入っていない文書・段階(その要素は予測に出ない)。`state`は束を作ったときの状態。"""

    key: str
    state: str


class ChangeRequestImpactRead(BaseModel):
    """CR の影響の予測。`saved`は判断の時点で保存したものか(偽なら今、基準の束から求めたもの)。
    `unpredicted_new_requirement`は、新しい要件を足す分が予測に入っていないこと(起点が無いため)。
    `origins`は予測に使った新しい要件の起点。"""

    number: int
    label: str
    baseline_number: int
    baseline_label: str
    saved: bool
    targets: list[PredictedTargetRead]
    items: list[PredictedItemRead]
    unpredicted_new_requirement: bool
    excluded: list[ExcludedItemRead]
    origins: list[ChangeRequestOriginRead] = []

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
                    origin_keys=list(i.origin_keys),
                )
                for i in prediction.items
            ],
            unpredicted_new_requirement=prediction.unpredicted_new_requirement,
            excluded=excluded,
            origins=[ChangeRequestOriginRead(key=o.key, label=o.label) for o in prediction.origins],
        )


class SummaryEdit(BaseModel):
    """説明書の要約を直す本文。"""

    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SUMMARY_MAX)
    ]


class ExplanationSummaryRead(BaseModel):
    """説明書の要約。`state`は今の5節と比べた状態(古い = 作った・確かめた後に5節が変わった)。
    `generated_text`は LLM の元の文、`text`は今の文。照合は、`text`に出る要件 ID・数字のうち
    説明書に無いもの。`needs_reconfirm`は、確認済みのまま説明書が変わって古くなったか(出力に
    載らないので、共有の前に確かめ直す)。"""

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
    needs_reconfirm: bool

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
            needs_reconfirm=needs_reconfirm(
                summary.status, summary.fingerprint, explanation.fingerprint
            ),
        )


class ScaleRemarkRead(BaseModel):
    """規模の数えられない理由、または注記1つ(`detail`は対象の段階・文書・要件 ID・単位)。"""

    code: str
    label: str
    detail: str

    @classmethod
    def build(cls, remark: ScaleRemark) -> "ScaleRemarkRead":
        return cls(code=remark.code, label=remark.label, detail=remark.detail)


class ScaleCountRead(BaseModel):
    """規模の種類1つ。`count`は数(数えられなければ null で、`uncountable`に理由)、`names`は
    数えたもの(表示名)、`notes`は数えたうえでの注記。"""

    kind: ScaleKind
    label: str
    count: int | None
    names: list[str]
    uncountable: list[ScaleRemarkRead]
    notes: list[ScaleRemarkRead]

    @classmethod
    def build(cls, count: ScaleCount) -> "ScaleCountRead":
        return cls(
            kind=count.kind,
            label=count.label,
            count=count.count,
            names=list(count.names),
            uncountable=[ScaleRemarkRead.build(r) for r in count.uncountable],
            notes=[ScaleRemarkRead.build(r) for r in count.notes],
        )


def scale_read(scale: Scale) -> list[ScaleCountRead]:
    return [ScaleCountRead.build(count) for count in scale.counts]


class OptionScaleRead(BaseModel):
    """案1つの規模(合意の前の案のある CR の説明書で、規模の表の1列)。"""

    number: int
    label: str
    name: str
    recommended: bool
    scale: list[ScaleCountRead]

    @classmethod
    def build(cls, option: OptionScale) -> "OptionScaleRead":
        return cls(
            number=option.number,
            label=option_label(option.number),
            name=option.name,
            recommended=option.recommended,
            scale=scale_read(option.scale),
        )


class ChangeRequestExplanationRead(BaseModel):
    """CR の説明書(プレビュー)。`markdown`は5節、`fingerprint`はその sha256、`output`は出力する
    md(確認済みで古くない要約だけを載せる)。`scale`は3節の規模(種類ごとの数と、数えられない
    理由)。`units`・`integration_tests`・`system_tests`・`has_test_spec`は規模の作業単位と
    テストの内訳(`has_test_spec`が偽なら、束にテスト仕様書が無くテストは数えていない)。
    `option_scales`は合意の前の案ごとの規模(案の無い CR・案を選んだ後は空。`scale`は案を
    合わせた範囲)。`summary`は要約(作っていなければ null)。"""

    number: int
    label: str
    filename: str
    markdown: str
    fingerprint: str
    output: str
    scale: list[ScaleCountRead]
    option_scales: list[OptionScaleRead] = []
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
        scale = explanation.scale
        return cls(
            number=change_request.number,
            label=change_request_label(change_request.number),
            filename=explanation_filename(change_request.number),
            markdown=explanation.markdown,
            fingerprint=explanation.fingerprint,
            output=output,
            scale=scale_read(scale),
            option_scales=[OptionScaleRead.build(o) for o in explanation.option_scales],
            units=list(scale.of("unit").names),
            integration_tests=list(scale.integration_tests),
            system_tests=list(scale.system_tests),
            has_test_spec=scale.has_test_spec,
            summary=(
                ExplanationSummaryRead.build(summary, summary_state, explanation)
                if summary is not None and summary_state is not None
                else None
            ),
        )


def change_request_label(number: int) -> str:
    return f"CR-{number}"


def _options_read(change_request: "ChangeRequest") -> ChangeRequestOptionsRead | None:
    options = options_from_json(change_request.options)
    return None if options.empty else ChangeRequestOptionsRead.from_options(options)


def summary_fields(change_request: "ChangeRequest") -> dict[str, Any]:
    """一覧と詳細に共通の項目(基準の束・入れた束は読み込み済みであること)。"""
    status: ChangeRequestStatus = change_request.status  # type: ignore[assignment]
    source: BackgroundSource = change_request.background_source  # type: ignore[assignment]
    baseline_number = change_request.baseline.number
    decision = None
    if change_request.decided_at is not None:
        decision = ChangeRequestDecisionRead(
            counterpart=change_request.decision_counterpart,
            decided_at=change_request.decided_at,
            memo=change_request.decision_memo or "",
            reason=change_request.decision_reason or "",
            recorded_by=change_request.decision_recorded_by,
            option=change_request.decision_option,
            source=change_request.decision_source,
            source_label=decision_source_label(change_request.decision_source),
            fingerprint=change_request.decision_fingerprint,
        )
    included = change_request.included_baseline
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
        "origins": [
            ChangeRequestOriginRead(key=str(o["key"]), label=str(o.get("label", "")))
            for o in change_request.origins or []
        ],
        "baseline_number": baseline_number,
        "baseline_label": f"B-{baseline_number}",
        "created_by": change_request.created_by,
        "created_at": change_request.created_at,
        "updated_at": change_request.updated_at,
        "decision": decision,
        "included_baseline_number": included.number if included is not None else None,
    }


def review_run_fields(run: "ReviewRun") -> dict[str, Any]:
    """レビューの回の要点の項目(指摘は読み込み済みであること)。"""
    return {
        "review_run_id": run.id,
        "gate": run.gate,
        "model": run.model,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "findings": len(run.findings),
        "adopted": sum(1 for f in run.findings if f.decision == "adopted"),
    }


class OptionDraftRead(BaseModel):
    """選択肢の LLM の下書き。`options`は LLM が作った案(規則に合わないこともある)、`status`は
    未確認 / 取り込み済み、`stale`は作った後に説明書が変わったか。照合の`problems`は取り込めない
    問題(案の規則・CR の対象に無い要件)、`unknown_*`は文に出る要件 ID・数字のうち説明書に
    無いもの(取り込みは止めない)。"""

    options: ChangeRequestOptionsRead
    status: OptionDraftStatus
    status_label: str
    stale: bool
    generated_model: str | None
    generated_by: uuid.UUID | None
    generated_at: datetime
    adopted_by: uuid.UUID | None
    adopted_at: datetime | None
    problems: list[str]
    unknown_requirement_ids: list[str]
    unknown_numbers: list[str]
    adoptable: bool

    @classmethod
    def build(
        cls,
        draft: "ChangeRequestOptionDraft",
        options: ChangeOptions,
        check: OptionDraftCheck,
        *,
        current_fingerprint: str,
    ) -> "OptionDraftRead":
        status: OptionDraftStatus = draft.status  # type: ignore[assignment]
        return cls(
            options=ChangeRequestOptionsRead.from_options(options),
            status=status,
            status_label=OPTION_DRAFT_STATUS_LABEL[status],
            stale=draft.fingerprint != current_fingerprint,
            generated_model=draft.generated_model,
            generated_by=draft.generated_by,
            generated_at=draft.generated_at,
            adopted_by=draft.adopted_by,
            adopted_at=draft.adopted_at,
            problems=list(check.problems),
            unknown_requirement_ids=list(check.unknown_requirement_ids),
            unknown_numbers=list(check.unknown_numbers),
            adoptable=check.adoptable,
        )
