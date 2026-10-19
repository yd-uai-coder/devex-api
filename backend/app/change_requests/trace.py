"""変更要求(CR)の、受付から検証までをたどる記録(純粋関数。docs/external_design.md 2.14節)。

CR の時系列の行(状態の遷移・反映・レビューを結ぶ・検証・束に入れる)の`refs`を、指す先の今の
状態で読み解く。文書の版は直近の数版しか残さないので、作った版が今も残っているかを示す。レビューは
CR に結んだ回の要点(区切り・モデル・状態・指摘と採用の数)を付ける。
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.change_requests.explanation import DOCUMENT_LABEL

ACTION_LABEL: dict[str, str] = {
    "create": "作成",
    "copy": "写して作成",
    "copied": "写した",
    "edit": "直した",
    "explain": "説明した",
    "agree": "合意",
    "reject": "却下",
    "withdraw": "取り下げ",
    "apply": "反映",
    "verify": "検証",
    "link_review": "レビューを結んだ",
    "baseline": "合意済みの版に入れた",
}


@dataclass(frozen=True)
class EventFacts:
    """時系列の1行の値(DB の行から写したもの)。"""

    action: str
    from_status: str | None
    to_status: str
    occurred_at: datetime
    actor_id: Any
    note: str
    refs: Mapping[str, Any] | None


@dataclass(frozen=True)
class ReviewFacts:
    """CR に結んだレビューの回の要点。"""

    review_run_id: str
    gate: str
    model: str
    status: str
    findings: int
    adopted: int


@dataclass(frozen=True)
class TraceDocument:
    """反映で作った文書の版1つ。`available`は今もその版が残っているか。"""

    doc_type: str
    label: str
    version: int
    available: bool


@dataclass(frozen=True)
class TraceStage:
    """反映で作り直した段階の下書きの版。"""

    stage: int
    version: int


@dataclass(frozen=True)
class TraceEntry:
    """たどる記録の1行(時系列の1行と、指す先を読み解いたもの)。"""

    position: int
    action: str
    action_label: str
    from_status: str | None
    to_status: str
    occurred_at: datetime
    actor_id: Any
    note: str
    model: str | None = None
    documents: tuple[TraceDocument, ...] = ()
    stage: TraceStage | None = None
    reviews: tuple[ReviewFacts, ...] = ()
    baseline_number: int | None = None


def trace_entries(
    events: Sequence[EventFacts],
    available_versions: Mapping[str, Collection[int]],
    reviews: Mapping[str, ReviewFacts],
) -> list[TraceEntry]:
    """時系列の行を、起きた順のたどる記録にする。`available_versions`は文書の種類 → 今ある版、
    `reviews`は回の id → 要点(結んだ回)。形の正しくない`refs`は読まない(記録で詳細を壊さない)。"""
    return [_entry(i, e, available_versions, reviews) for i, e in enumerate(events)]


def _entry(
    position: int,
    event: EventFacts,
    available_versions: Mapping[str, Collection[int]],
    reviews: Mapping[str, ReviewFacts],
) -> TraceEntry:
    refs = event.refs or {}
    model = refs.get("model")
    run_ids = [refs["review_run_id"]] if "review_run_id" in refs else refs.get("review_run_ids")
    baseline = refs.get("baseline_number")
    return TraceEntry(
        position=position,
        action=event.action,
        action_label=ACTION_LABEL.get(event.action, event.action),
        from_status=event.from_status,
        to_status=event.to_status,
        occurred_at=event.occurred_at,
        actor_id=event.actor_id,
        note=event.note,
        model=model if isinstance(model, str) else None,
        documents=tuple(_documents(refs.get("documents"), available_versions)),
        stage=_stage(refs) if event.action == "apply" else None,
        reviews=tuple(
            reviews[str(run_id)] for run_id in run_ids or [] if str(run_id) in reviews
        ),
        baseline_number=baseline if isinstance(baseline, int) else None,
    )


def _documents(
    value: Any, available_versions: Mapping[str, Collection[int]]
) -> list[TraceDocument]:
    documents: list[TraceDocument] = []
    for item in value if isinstance(value, list) else []:
        try:
            doc_type = str(item["doc_type"])
            version = int(item["version"])
        except (KeyError, TypeError, ValueError):
            continue
        documents.append(
            TraceDocument(
                doc_type,
                DOCUMENT_LABEL.get(doc_type, doc_type),
                version,
                version in available_versions.get(doc_type, ()),
            )
        )
    return documents


def _stage(refs: Mapping[str, Any]) -> TraceStage | None:
    stage, version = refs.get("stage"), refs.get("version")
    if isinstance(stage, int) and isinstance(version, int):
        return TraceStage(stage, version)
    return None
