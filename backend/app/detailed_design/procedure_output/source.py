"""実装手順書の出力の入力と、未定義・要決定の集約(純粋関数)。

docs/internal_design.md 3.3節「5. 実装手順書」の「出力」。

手順書の出力(zip の`implementation_procedure/`と、画面の「AI 向けにコピー」)は、段階8の手順書と、
段階8の土台(`ProcedureBasis`。詳細設計モードは承認済みの段階1〜7、簡易モードは4文書)から決定的に
組み立てる表示で、文書としては保存しない。ここでは、組み立てに使う値を1つの
`ProcedureOutputSource`にまとめる。DB の読み取りはサービスが行う。

- 単位の一覧は作業単位の並び順(`plan_units`。依存順)。手順書は、単位の ID とタスク名が作業単位と
  合うものだけを使う(`documented_unit_ids`。合わない手順書は検証のエラーで、作り直しの対象)。
- 参照する設計の展開は、画面の単位の詳細・生成の入力と同じ`ProcedureBasis.context`を使う。
- モードで変わる文言(作業単位の出どころ・直す先)は、土台の`labels`から書く。
- 未定義・要決定は、段階8の検証の指摘(重要度のあるもの)と、手順書の AI の指摘を1つにまとめる。
  並びと数え方は画面(devex-ui の`procedureDocOps.collectFindings`)と同じにする。
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.detailed_design.plan import PlanModel
from app.detailed_design.procedure_basis import DETAILED_LABELS, ProcedureBasis, ProcedureLabels
from app.detailed_design.procedure_doc import (
    DESIGN_DOCUMENT_LABELS,
    FINDING_LEVELS,
    PROCEDURE_DOC_STAGE,
    DesignDocument,
    FindingLevel,
    PlanUnit,
    ProcedureDocModel,
    UnitProcedure,
    documented_unit_ids,
    plan_units,
)
from app.detailed_design.procedure_doc_refs import UnitContext
from app.detailed_design.stages import StageState
from app.detailed_design.validation import StageIssue
from app.uml.generation.sections import extract_section

# 未定義の出どころ。check = 決定的な検証、ai = 手順書を生成した AI の指摘
FindingOrigin = Literal["check", "ai"]
ORIGIN_LABELS: dict[FindingOrigin, str] = {"check": "検証", "ai": "AI"}
LEVEL_LABELS: dict[FindingLevel, str] = {"critical": "最重要", "major": "中程度", "minor": "軽微"}

# 要件定義書の MoSCoW の節(手順書の対象外を書くために読む)と、対象外にする優先度
REQUIREMENTS_SCOPE_SECTION = "1.4"
OUT_OF_SCOPE_PRIORITIES: tuple[str, ...] = ("Should", "Could", "Won't")

# MoSCoW の行の、優先度と中身の区切り(半角・全角のコロン)
_SCOPE_SEPARATOR = re.compile(r"[:：]")

# ファイル名に使えない文字(と空白)。`_`に置き換える
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\s]+')


@dataclass(frozen=True)
class UnitFinding:
    """未定義・要決定の1件。`unit`が None なら単位によらない指摘。`fix_stage`は直す先の段階
    (8 なら直す先の段階が無い)。簡易モードは直す先を文書`fix_document`で示す。"""

    level: FindingLevel
    origin: FindingOrigin
    unit: str | None
    target: str
    message: str
    fix_stage: int
    fix_document: DesignDocument | None = None


@dataclass(frozen=True)
class ProcedureOutputSource:
    """手順書の出力の組み立てに使う値の全部。

    - `state`: 段階8の状態。zip は`approved`のときだけ手順書を組み立てる。画面のコピーは保存済みの
      手順書から作り、`approved`でなければ先頭で警告する。
    - `units`: 作業単位(並び順)。`procedures`・`contexts`は手順書のある単位だけ。
    - `findings`: 未定義・要決定(重要度の順)。
    - `out_of_scope`: 要件定義の Should / Could / Won't の行(手順書を作らない機能)。
    - `labels`・`environment`・`rules`: モードで変わる文言と、実装前提の本文・実装ルール
      (`ProcedureBasis`から)。
    """

    title: str
    state: StageState
    plan: PlanModel
    units: tuple[PlanUnit, ...] = ()
    procedures: Mapping[str, UnitProcedure] = field(default_factory=dict)
    contexts: Mapping[str, UnitContext] = field(default_factory=dict)
    findings: tuple[UnitFinding, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    labels: ProcedureLabels = DETAILED_LABELS
    environment: str = ""
    rules: tuple[str, ...] = ()

    @property
    def approved(self) -> bool:
        return self.state == "approved"

    def unit(self, unit_id: str) -> PlanUnit | None:
        return next((u for u in self.units if u.unit_id == unit_id), None)

    def milestone_name(self, unit: PlanUnit) -> str:
        """単位のマイルストーンの名前。"""
        return self.plan.milestones[int(unit.milestone.removeprefix("M-")) - 1].name


def procedure_output_source(
    title: str,
    state: StageState,
    basis: ProcedureBasis,
    model: Mapping[str, Any] | None,
    issues: Sequence[StageIssue],
    requirements: str = "",
) -> ProcedureOutputSource:
    """段階8の状態・手順書(`model`)・検証の指摘と、段階8の土台(`basis`)・要件定義の本文から
    `ProcedureOutputSource`を作る。`model`は zip なら承認済みのもの、画面のコピーなら
    保存済みのもの。"""
    plan = basis.plan
    doc = ProcedureDocModel.model_validate(model or {})
    units = tuple(plan_units(plan))
    documented = documented_unit_ids(plan, doc)
    procedures = {u.unit_id: u for u in doc.units if u.unit_id in documented}
    return ProcedureOutputSource(
        title=title,
        state=state,
        plan=plan,
        units=units,
        procedures=procedures,
        contexts={u.unit_id: basis.context(u) for u in units if u.unit_id in procedures},
        findings=tuple(collect_findings(issues, procedures.values())),
        out_of_scope=tuple(out_of_scope_lines(requirements)),
        labels=basis.labels,
        environment=basis.environment,
        rules=basis.rules,
    )


def collect_findings(
    issues: Sequence[StageIssue], procedures: Iterable[UnitProcedure]
) -> list[UnitFinding]:
    """検証の指摘のうち重要度のあるもの(設計の不足)と、手順書の AI の指摘を、重要度の順に並べる
    (同じ重要度の中では検証 → AI、元の順)。重要度の無い検証の指摘(手順書そのもののエラー)は
    含めない。"""
    checks = [
        UnitFinding(
            level=issue.level,
            origin="check",
            unit=issue.unit,
            target=issue.target or "",
            message=issue.message,
            fix_stage=issue.fix_stage or PROCEDURE_DOC_STAGE,
            fix_document=issue.fix_document,
        )
        for issue in issues
        if issue.level is not None
    ]
    ai = [
        UnitFinding(
            level=finding.level,
            origin="ai",
            unit=procedure.unit_id,
            target=finding.target,
            message=finding.message,
            fix_stage=finding.fix_stage,
            fix_document=finding.fix_document,
        )
        for procedure in procedures
        for finding in procedure.findings
    ]
    return sorted([*checks, *ai], key=lambda f: FINDING_LEVELS.index(f.level))


def unit_findings(source: ProcedureOutputSource, unit_id: str | None) -> list[UnitFinding]:
    """単位の未定義(`unit_id`が None なら単位によらないもの)。"""
    return [f for f in source.findings if f.unit == unit_id]


def count_by_level(findings: Sequence[UnitFinding]) -> dict[FindingLevel, int]:
    counts: dict[FindingLevel, int] = dict.fromkeys(FINDING_LEVELS, 0)
    for finding in findings:
        counts[finding.level] += 1
    return counts


def count_text(findings: Sequence[UnitFinding]) -> str:
    """重要度ごとの件数(`最重要1・中程度2`。0件の重要度は書かない。無ければ「なし」)。"""
    counts = count_by_level(findings)
    parts = [f"{LEVEL_LABELS[level]}{counts[level]}" for level in FINDING_LEVELS if counts[level]]
    return "・".join(parts) or "なし"


def fix_stage_text(stage: int) -> str:
    """直す先の段階の表示(段階8は直す先が無いので「—」)。"""
    return f"段階{stage}" if stage < PROCEDURE_DOC_STAGE else "—"


def fix_target_text(finding: UnitFinding) -> str:
    """直す先の表示。文書があれば文書の名前(簡易モード)、無ければ段階(`fix_stage_text`)。"""
    if finding.fix_document is not None:
        return DESIGN_DOCUMENT_LABELS[finding.fix_document]
    return fix_stage_text(finding.fix_stage)


def out_of_scope_lines(requirements: str) -> list[str]:
    """要件定義書の 1.4節(MoSCoW)から、Should / Could / Won't の行を取り出す(行頭の`-`と
    太字の印を除く)。中身の無い行(`Could have: `)は除く。節が無ければ空。"""
    section = extract_section(requirements, REQUIREMENTS_SCOPE_SECTION)
    lines: list[str] = []
    for line in section.splitlines():
        text = line.strip().lstrip("-*").strip().replace("**", "")
        if not any(text.startswith(priority) for priority in OUT_OF_SCOPE_PRIORITIES):
            continue
        if _SCOPE_SEPARATOR.split(text, maxsplit=1)[-1].strip():
            lines.append(text)
    return lines


def unit_filename(unit: PlanUnit) -> str:
    """単位の md のファイル名(`M-01-T02_予約を登録する.md`)。パスに使えない文字と空白は`_`に。"""
    title = _UNSAFE_FILENAME.sub("_", unit.task.title.strip()).strip("_")
    return f"{unit.unit_id}_{title}.md" if title else f"{unit.unit_id}.md"
