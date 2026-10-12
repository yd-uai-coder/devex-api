"""AI の下書きから実装計画書の model を組み立てる(純粋関数)。

- 依存先: AI が書いた単位の ID(`M-01-T01`)を、並び順から導いた ID に読み替える
  (`renamed_task_ids`。書かれた ID が並び順と違っても、指したタスクを保つ)。
- 処理: `df-01`を`DF-1`にそろえる。内部設計書に在るかは検証が見る。
- 要件: 要件ID の形(`FR-001`)にそろえ、形の違う値は捨てる。要件定義書に在るかは検証が見る。
- キー: 前の版に model があれば、タスク名で前の版のキーを引き継ぎ(`carry_over_unit_keys`)、
  残りに次の番号を振る。前の版に model が無ければ全部新しく振る。
- 名前の無いマイルストーン・タスクは捨てる。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.detailed_design.plan import (
    PRIORITIES,
    Milestone,
    PlanModel,
    PlanTask,
    Priority,
    Risk,
    UnitKind,
    assign_unit_keys,
    carry_over_unit_keys,
    renamed_task_ids,
)
from app.detailed_design.requirement_trace import normalize_req_id
from app.documents.implementation_plan.model import ImplementationPlanModel
from app.documents.internal_design.model import dataflow_id, dataflow_seq
from app.documents.requirements.model import RequirementsModel

_EMPTY_VALUES = frozenset({"", "なし", "無し", "—", "-", "n/a"})


@dataclass(frozen=True)
class TaskDraft:
    """AI が下書きしたタスク1つ(`id`は AI が書いた単位の ID)。"""

    id: str
    kind: str
    title: str
    dataflow_ids: tuple[str, ...] = ()
    req_ids: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    config_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class MilestoneDraft:
    name: str
    priority: str = "Must"
    goal: str = ""
    tasks: tuple[TaskDraft, ...] = ()


@dataclass(frozen=True)
class RiskDraft:
    risk: str
    mitigation: str = ""


@dataclass(frozen=True)
class ImplementationPlanDraft:
    """AI の下書き1回分(構造化出力から`drafting.to_draft`で作る)。"""

    phases: str = ""
    milestones: tuple[MilestoneDraft, ...] = ()
    environment: str = ""
    risks: tuple[RiskDraft, ...] = ()


def _values(values: Iterable[str]) -> list[str]:
    items = (v.replace("**", "").strip().strip("`").strip() for v in values)
    return list(dict.fromkeys(v for v in items if v.lower() not in _EMPTY_VALUES))


def _dataflow_ids(values: Iterable[str]) -> list[str]:
    ids: list[str] = []
    for value in _values(values):
        seq = dataflow_seq(value)
        ids.append(dataflow_id(seq) if seq is not None else value)
    return list(dict.fromkeys(ids))


def _req_ids(values: Iterable[str]) -> list[str]:
    ids = (normalize_req_id(v) for v in _values(values))
    return list(dict.fromkeys(i for i in ids if i is not None))


def _kind(value: str) -> UnitKind:
    return "base" if value.strip() in ("base", "基盤") else "feature"


def _priority(value: str) -> Priority:
    return value if value in PRIORITIES else "Must"  # type: ignore[return-value]


def _milestones(drafts: Sequence[MilestoneDraft]) -> list[Milestone]:
    kept = [
        (m, [t for t in m.tasks if t.title.strip()]) for m in drafts if m.name.strip()
    ]
    renames = renamed_task_ids([[t.id for t in tasks] for _, tasks in kept])
    return [
        Milestone(
            name=m.name.strip(),
            goal=m.goal.strip(),
            priority=_priority(m.priority),
            tasks=[
                PlanTask(
                    kind=_kind(t.kind),
                    title=t.title.strip(),
                    function_ids=_dataflow_ids(t.dataflow_ids),
                    depends_on=[renames.get(d.upper(), d.upper()) for d in _values(t.depends_on)],
                    modules=_values(t.modules),
                    config_files=_values(t.config_files),
                    req_ids=_req_ids(t.req_ids),
                )
                for t in tasks
            ],
        )
        for m, tasks in kept
    ]


def build_model(
    draft: ImplementationPlanDraft,
    previous: ImplementationPlanModel | None,
    *,
    dataflow_ids: Sequence[str] = (),
    requirements: RequirementsModel | None = None,
) -> ImplementationPlanModel:
    """下書きから model を作る。生成した時点の内部設計書の処理別データフローの ID と、要件定義書の
    要件ID を写して持つ(検証が読む)。"""
    plan = PlanModel(milestones=_milestones(draft.milestones))
    plan = (
        carry_over_unit_keys(plan, previous.to_plan())
        if previous is not None
        else assign_unit_keys(plan)
    )
    rows = requirements.rows if requirements else []
    return ImplementationPlanModel(
        phases=draft.phases.strip(),
        milestones=plan.milestones,
        environment=draft.environment.strip(),
        risks=[
            Risk(risk=r.risk.strip(), mitigation=r.mitigation.strip())
            for r in draft.risks
            if r.risk.strip()
        ],
        next_unit_seq=plan.next_unit_seq,
        known_dataflow_ids=list(dict.fromkeys(dataflow_ids)),
        known_req_ids=[r.id for r in rows],
        must_fr_ids=[r.id for r in rows if r.kind == "FR" and r.priority == "Must"],
    )
