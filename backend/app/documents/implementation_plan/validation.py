"""実装計画書の検証(純粋関数)。指摘は警告だけで、生成も保存も止めない。

処理別データフローの ID・要件ID は生成した時点の写し(`known_dataflow_ids`・`known_req_ids`・
`must_fr_ids`)と照らす。写しが空なら、その照合はしない。

段階8の検証(`app.detailed_design.validation`の簡易モードの部分)も同じ書き落としを単位ごとに
指摘する。こちらは文書のプレビューに出すため、同じ種類の指摘を1件にまとめる。
"""

from app.detailed_design.plan import milestone_id
from app.detailed_design.plan_goal import uncovered_goal_terms
from app.detailed_design.procedure_doc import PlanUnit, plan_units
from app.detailed_design.requirement_trace import unit_req_ids
from app.documents.implementation_plan.model import ImplementationPlanModel
from app.documents.issues import DocumentIssue, IssueLevel


def row_counts(model: ImplementationPlanModel) -> dict[str, int]:
    """マイルストーン・タスク(作業単位)・リスクの行数。"""
    return {
        "milestones": len(model.milestones),
        "tasks": sum(len(m.tasks) for m in model.milestones),
        "risks": len(model.risks),
    }


def _issue(level: IssueLevel, code: str, lead: str, targets: list[str], tail: str = "") -> list[
    DocumentIssue
]:
    if not targets:
        return []
    targets = list(dict.fromkeys(targets))
    return [DocumentIssue(level, code, f"{lead}: " + "・".join(targets) + tail, tuple(targets))]


def _dependency_issues(units: list[PlanUnit]) -> list[DocumentIssue]:
    order = {u.unit_id: i for i, u in enumerate(units)}
    unknown: list[str] = []
    forward: list[str] = []
    for unit in units:
        for dependency in (d.strip() for d in unit.task.depends_on if d.strip()):
            if dependency not in order:
                unknown.append(f"{unit.unit_id} → {dependency}")
            elif order[dependency] >= order[unit.unit_id]:
                forward.append(f"{unit.unit_id} → {dependency}")
    return [
        *_issue("major", "UNKNOWN_DEPENDENCY", "依存先がタスクの一覧にありません", unknown),
        *_issue(
            "major",
            "FORWARD_DEPENDENCY",
            "依存先が自分か後ろのタスクです",
            forward,
            "(依存先は前に並べます)",
        ),
    ]


def _requirement_issues(model: ImplementationPlanModel, units: list[PlanUnit]) -> list[
    DocumentIssue
]:
    if not model.known_req_ids:
        return []
    reqs = unit_req_ids(units)
    linked = {req for values in reqs.values() for req in values}
    known = set(model.known_req_ids)
    return [
        *_issue(
            "major",
            "MUST_REQUIREMENT_UNPLANNED",
            "担うタスクの無い Must の機能要件があります",
            [r for r in model.must_fr_ids if r not in linked],
        ),
        *_issue(
            "minor",
            "UNLINKED_UNIT",
            "要件の無い機能のタスクがあります",
            [u.unit_id for u in units if u.task.kind == "feature" and not reqs[u.unit_id]],
        ),
        *_issue(
            "minor",
            "UNKNOWN_REQUIREMENT_ID",
            "要件定義書に無い要件ID があります",
            [r for u in units for r in reqs[u.unit_id] if r not in known],
        ),
    ]


def validate_implementation_plan(model: ImplementationPlanModel) -> list[DocumentIssue]:
    units = plan_units(model.to_plan())
    if not units:
        return [
            DocumentIssue(
                "critical",
                "NO_TASKS",
                "タスク(作業単位)がありません(実装計画書を再生成してください)",
            )
        ]
    issues = _issue(
        "major",
        "FEATURE_WITHOUT_DATAFLOW",
        "処理(DF)の無い機能のタスクがあります",
        [u.unit_id for u in units if u.task.kind == "feature" and not u.task.function_ids],
        "(内部設計書の処理別データフローに、その処理があるかを見直してください)",
    )
    planned = [f for u in units for f in u.task.function_ids]
    if model.known_dataflow_ids:
        known = set(model.known_dataflow_ids)
        issues += _issue(
            "major",
            "UNKNOWN_DATAFLOW",
            "内部設計書に無い処理があります",
            [f for f in planned if f not in known],
        )
        issues += _issue(
            "minor",
            "UNPLANNED_DATAFLOW",
            "どのタスクにも入っていない内部設計書の処理があります",
            [f for f in model.known_dataflow_ids if f not in set(planned)],
        )
    return (
        issues
        + _dependency_issues(units)
        + _requirement_issues(model, units)
        + _goal_issues(model)
    )


def _goal_issues(model: ImplementationPlanModel) -> list[DocumentIssue]:
    """ゴールが挙げるのに、そのマイルストーンのどのタスクも作らないもの(`plan_goal`)。"""
    targets = [
        f"{milestone_id(i)}({'・'.join(terms)})"
        for i, m in enumerate(model.milestones)
        if (terms := uncovered_goal_terms(m))
    ]
    return _issue(
        "major",
        "GOAL_WITHOUT_TASK",
        "ゴールに書いたものを作るタスクが、そのマイルストーンにありません",
        targets,
        "(作るタスクを足すか、ゴールから外してください)",
    )
