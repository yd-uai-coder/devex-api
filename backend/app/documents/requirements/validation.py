"""要件定義書の検証(純粋関数)。指摘は警告だけで、生成も保存も止めない。

ID の重複・形の誤りは`assemble`が決定的に振るので起きない。ここでは、利用者に確かめてほしい
こと(推測の要件・未決の必須の観点・Must の機能要件が無い)を挙げる。
"""

from dataclasses import dataclass
from typing import Literal

from app.documents.requirements.model import RequirementsModel, RequirementSource
from app.hearing import POINT_DEFS, as_scale, is_required

IssueLevel = Literal["critical", "major", "minor"]


@dataclass(frozen=True)
class RequirementIssue:
    level: IssueLevel
    code: str
    message: str
    targets: tuple[str, ...] = ()


def source_counts(model: RequirementsModel) -> dict[RequirementSource, int]:
    """要件の行の出どころの件数(効果測定の「推測の割合」にも使う)。"""
    counts: dict[RequirementSource, int] = {"user": 0, "adopted": 0, "assumed": 0}
    for row in model.rows:
        counts[row.source] += 1
    return counts


def validate_requirements(model: RequirementsModel) -> list[RequirementIssue]:
    """生成した時点の観点の表(`model.point_statuses`)に対して検証する。"""
    statuses = model.point_statuses
    scale = as_scale(model.scale)
    issues: list[RequirementIssue] = []
    if not any(r.kind == "FR" and r.priority == "Must" for r in model.rows):
        issues.append(
            RequirementIssue("major", "NO_MUST_REQUIREMENT", "Must の機能要件がありません。")
        )
    open_required = [
        d
        for d in POINT_DEFS
        if is_required(d.key, scale) and statuses.get(d.key, "open") == "open"
    ]
    if open_required:
        issues.append(
            RequirementIssue(
                "major",
                "REQUIRED_POINT_OPEN",
                "必須の観点が未決のまま生成しました: "
                + "・".join(d.label for d in open_required)
                + "(チャットで答えて再生成してください)",
                tuple(d.key for d in open_required),
            )
        )
    not_applicable = {k for k, status in statuses.items() if status == "not_applicable"}
    on_not_applicable = [r.id for r in model.rows if r.points and set(r.points) <= not_applicable]
    if on_not_applicable:
        issues.append(
            RequirementIssue(
                "minor",
                "REQUIREMENT_ON_NOT_APPLICABLE_POINT",
                "対象外とした観点だけを根拠にした要件があります: " + "・".join(on_not_applicable),
                tuple(on_not_applicable),
            )
        )
    assumed = [r.id for r in model.rows if r.source == "assumed"]
    if assumed:
        issues.append(
            RequirementIssue(
                "minor",
                "ASSUMED_REQUIREMENTS",
                f"推測の要件が{len(assumed)}件あります(利用者に確かめてください)。",
                tuple(assumed),
            )
        )
    return issues
