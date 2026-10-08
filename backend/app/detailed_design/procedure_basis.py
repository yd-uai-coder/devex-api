"""段階8(実装手順書)の土台 ── モードごとの作業単位・参照・共通の節(純粋関数)。

docs/internal_design.md 3.3節「5. 実装手順書」。

段階8の部品(検証・生成・出力・画面の単位の詳細)は、作業単位と参照する設計をここから受け取る。
モードによって出どころだけが違い、手順書の形とその後の処理は同じにする(作成方針 3章)。

- 詳細設計モード: 作業単位は段階7、参照は段階3〜6、共通の節は段階7(07章・開発環境)と段階4。
- 簡易ドキュメントモード: 作業単位は実装計画書の WBS(`parse_wbs`)、参照は内部設計書
  (`parse_internal_design`)、共通の節は内部設計書 3.1・3.4節と実装計画書 4.3節。WBS の
  モジュールは、段階7の下書きと同じ規則でモジュール一覧のパスにそろえる(`normalize_plan`)。

検証(validation.py)がこのモジュールを使うので、ここから validation は import しない。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.detailed_design.plan import PLAN_STAGE, PlanModel, PlanTask, normalize_plan
from app.detailed_design.procedure_doc import (
    DesignIndex,
    DesignRef,
    PlanUnit,
    design_index,
    plan_units,
    unit_refs,
)
from app.detailed_design.procedure_doc_refs import UnitContext, unit_context
from app.detailed_design.simple_procedure import (
    SimpleDesignBook,
    WbsIssue,
    parse_internal_design,
    parse_wbs,
    simple_unit_context,
    simple_unit_refs,
)
from app.detailed_design.stages import ProjectMode

# 段階4のモジュール一覧から引く実装ルール(どの単位にも共通)
MODULE_RULE = "モジュールは、段階4のモジュール一覧の依存先にだけ依存する(層を飛び越さない)。"
SIMPLE_MODULE_RULE = (
    "モジュールは、内部設計書のモジュール一覧の依存先にだけ依存する(層を飛び越さない)。"
)


@dataclass(frozen=True)
class ProcedureLabels:
    """出力・検証の文言のうち、モードで変わるもの。

    - `plan`: 作業単位の出どころ(`段階7`・`実装計画書`)
    - `sources`: 手順書を組み立てた元(`段階1〜7`・`4文書`)
    - `environment`・`rules`: index の「実装前提・制約」の小見出し
    - `fix_heading`・`fix_guide`: 未定義の表の「直す先」の列名と、直し方の説明
    - `module_rule`: モジュールの依存の実装ルール(どの単位にも共通。AI 向けの版にも書く)
    """

    plan: str
    sources: str
    environment: str
    rules: str
    fix_heading: str
    fix_guide: str
    module_rule: str


DETAILED_LABELS = ProcedureLabels(
    plan="段階7",
    sources="段階1〜7",
    environment="技術スタック・開発環境(段階7)",
    rules="実装ルール(段階4・07章より)",
    fix_heading="直す段階",
    fix_guide="「直す段階」へ戻って設計を直す。直した段階は差し戻され、この手順書は「古い」になる。",
    module_rule=MODULE_RULE,
)
SIMPLE_LABELS = ProcedureLabels(
    plan="実装計画書",
    sources="4文書",
    environment="技術スタック・開発環境(実装計画書 4.3・内部設計書 3.1)",
    rules="実装ルール(内部設計書 3.3・3.4より)",
    fix_heading="直す先",
    fix_guide=(
        "「直す先」の文書を再生成して設計を直す(手順の無い単位は、詳細設計モードで詰めることも"
        "検討する)。文書を再生成すると、この手順書は「古い」になる。"
    ),
    module_rule=SIMPLE_MODULE_RULE,
)


@dataclass(frozen=True)
class ProcedureBasis:
    """段階8の土台。`plan`は作業単位(段階7と同じ形)、`environment`・`rules`は共通の節の本文。

    詳細設計モードは`stages`と`index`を、簡易モードは`book`と`wbs_issues`(WBS の解析の指摘)を持つ。
    """

    mode: ProjectMode
    plan: PlanModel
    labels: ProcedureLabels
    environment: str = ""
    rules: tuple[str, ...] = ()
    stages: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
    index: DesignIndex = field(default_factory=DesignIndex)
    book: SimpleDesignBook = field(default_factory=SimpleDesignBook)
    wbs_issues: tuple[WbsIssue, ...] = ()

    @property
    def units(self) -> list[PlanUnit]:
        """作業単位(計画の並び順 = 依存順)。"""
        return plan_units(self.plan)

    def refs(self, task: PlanTask) -> list[DesignRef]:
        """単位が参照する設計。"""
        if self.mode == "simple":
            return simple_unit_refs(task, self.book)
        return unit_refs(task, self.index)

    def context(self, unit: PlanUnit) -> UnitContext:
        """単位の参照の展開と共通の節(生成の入力・画面の単位の詳細・AI 向けの出力が使う)。"""
        if self.mode == "simple":
            return simple_unit_context(unit, self.book, self.plan.environment)
        return unit_context(unit, self.stages)


def procedure_basis(
    mode: ProjectMode,
    stages: Mapping[int, Mapping[str, Any]],
    documents: Mapping[str, str],
) -> ProcedureBasis:
    """モードの土台を作る。詳細設計モードは承認済みの段階(`stages`)から、簡易モードは
    生成済みの文書(`documents`。文書の種類 → 本文)から。"""
    if mode == "simple":
        return _simple_basis(documents)
    plan = PlanModel.model_validate(stages.get(PLAN_STAGE) or {})
    rules = (MODULE_RULE, *(f"07章 {row.topic}: {row.policy}" for row in plan.crosscutting))
    return ProcedureBasis(
        mode="detailed",
        plan=plan,
        labels=DETAILED_LABELS,
        environment=plan.environment.strip(),
        rules=rules,
        stages=stages,
        index=design_index(stages),
    )


def _simple_basis(documents: Mapping[str, str]) -> ProcedureBasis:
    book = parse_internal_design(
        documents.get("internal_design", ""), documents.get("external_design", "")
    )
    wbs = parse_wbs(documents.get("implementation_plan", ""))
    plan = normalize_plan(wbs.plan, list(book.modules))
    environment = "\n\n".join(t for t in (plan.environment, book.architecture.strip()) if t)
    rules = (SIMPLE_MODULE_RULE, *(f"3.4 {line}" for line in _bullets(book.error_policy)))
    return ProcedureBasis(
        mode="simple",
        plan=plan,
        labels=SIMPLE_LABELS,
        environment=environment,
        rules=rules,
        book=book,
        wbs_issues=wbs.issues,
    )


def _bullets(text: str) -> list[str]:
    """箇条書きの行の中身(行頭の`-`・`*`を除く)。"""
    lines = (line.strip() for line in text.splitlines())
    return [line.lstrip("-*").strip() for line in lines if line[:1] in ("-", "*")]
