"""実装計画書の model から md を組み立てる(純粋関数)。

見出しと 4.2 の WBS の書式は、model にする前の実装計画書のプロンプトが決めていた形のまま
(`### M-01: <名前> ── 【Must】`・`- [ ] M-01-T01 [機能] <タスク名>`・字下げした欄)。model の無い
古い版を読む解析(`app.detailed_design.simple_procedure.parse_wbs`)が、この md も同じ計画として
読める。単位の ID は並び順から導く(段階7と同じ)。
"""

from collections.abc import Sequence

from app.detailed_design.plan import PlanTask, milestone_id, task_id
from app.documents.implementation_plan.model import ImplementationPlanModel
from app.documents.internal_design.markdown import body_text

TITLE = "# 4. 実装計画書"
PHASES_HEADING = "## 4.1 開発フェーズ分割・マイルストーン"
WBS_HEADING = "## 4.2 タスク分解（WBS）"
ENVIRONMENT_HEADING = "## 4.3 開発環境・CI/CD・事前準備事項"
RISK_HEADING = "## 4.4 想定リスクと対策（トレードオフ・後回し候補）"
_KIND_LABELS = {"feature": "機能", "base": "基盤"}
_NONE = "なし"


def _values(values: Sequence[str]) -> str:
    return ", ".join(v for v in values if v.strip()) or _NONE


def _task(unit_id: str, task: PlanTask) -> list[str]:
    return [
        f"- [ ] {unit_id} [{_KIND_LABELS[task.kind]}] {task.title}",
        f"  - 処理: {_values(task.function_ids)}",
        f"  - 要件: {_values(task.req_ids)}",
        f"  - 依存: {_values(task.depends_on)}",
        f"  - モジュール: {_values(task.modules)}",
        f"  - 環境・設定: {_values(task.config_files)}",
    ]


def _wbs(model: ImplementationPlanModel) -> list[str]:
    if not model.milestones:
        return ["(なし)"]
    lines: list[str] = []
    for m, milestone in enumerate(model.milestones):
        lines += [f"### {milestone_id(m)}: {milestone.name} ── 【{milestone.priority}】"]
        if milestone.goal:
            lines.append(f"- ゴール: {milestone.goal}")
        for t, task in enumerate(milestone.tasks):
            lines += _task(task_id(m, t), task)
        lines.append("")
    return lines[:-1]


def _risks(model: ImplementationPlanModel) -> list[str]:
    if not model.risks:
        return ["(なし)"]
    lines: list[str] = []
    for n, risk in enumerate(model.risks, start=1):
        lines += [f"**リスク{n}: {risk.risk}**", "", f"*対策*: {risk.mitigation or '—'}", ""]
    return lines[:-1]


def render_implementation_plan(model: ImplementationPlanModel) -> str:
    """実装計画書の md。"""
    lines = [TITLE, "", PHASES_HEADING, "", body_text(model.phases)]
    lines += ["", WBS_HEADING, "", *_wbs(model)]
    lines += ["", ENVIRONMENT_HEADING, "", body_text(model.environment)]
    lines += ["", RISK_HEADING, "", *_risks(model)]
    return "\n".join(lines) + "\n"
