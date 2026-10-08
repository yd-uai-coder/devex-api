"""実装手順書の Markdown を組み立てる(純粋関数)。

docs/external_design.md 2.8節「出力」。作成方針
(appendix/devex_implementation_procedure_guideline.md)5・9・12・17章の形で、次の3つを作る。

- `to_index_markdown`: `implementation_procedure/index.md`(実装概要・実装前提・単位の一覧・
  未定義の一覧・完了条件)。段階8が承認済みでなければ「未承認」とだけ書く(詳細設計書の章と同じ)。
- `to_unit_markdown`: 単位ごとの md(人向け)。参照する設計は ID(見出し)だけを書き、中身を
  書き写さない。段階5の手順のシーケンス図だけは添える。
- `to_ai_markdown`: AI 向けの版(`ai/<単位ID>.md`と、画面の「AI 向けにコピー」)。人向けと同じ
  中身に、参照する設計を展開して添える。段階8が承認済みでない・未定義が残るときは、先頭で警告する
  (渡すのは止めない)。

表は詳細設計書の md と同じ`md_table`で書く。
"""

from collections.abc import Sequence

from app.detailed_design.document.markdown import md_table
from app.detailed_design.document.views import UNIT_KIND_LABELS
from app.detailed_design.plan import PlanModel, milestone_id
from app.detailed_design.procedure_doc import PlanUnit, UnitFileKind, UnitProcedure
from app.detailed_design.procedure_doc_refs import ExpandedRef, UnitContext
from app.detailed_design.procedure_output.source import (
    LEVEL_LABELS,
    ORIGIN_LABELS,
    ProcedureOutputSource,
    UnitFinding,
    count_by_level,
    count_text,
    fix_target_text,
    unit_filename,
    unit_findings,
)

PROCEDURE_UNAPPROVED_TEXT = (
    "未承認(段階8が承認されていません。承認すると、実装手順書が組み立てられます)"
)
NOT_GENERATED_TEXT = "(未生成)"

# index の単位の一覧の列(HTML も同じ)
UNIT_LIST_HEADERS: tuple[str, ...] = (
    "単位",
    "種別",
    "対象の処理",
    "依存",
    "作成・変更するファイル(依存順)",
    "確認方法",
    "未定義",
)

# 単位の完了条件(すべての単位に共通。作成方針12章)
COMPLETION_CRITERIA: tuple[str, ...] = (
    "この単位のテストが全件成功する",
    "既存のテストが壊れていない",
    "lint・型チェックが成功する",
    "(テーブルを変える単位)マイグレーションの適用が成功する",
    "開発環境で起動し、確認方法の操作ができる",
)
DECIDED_CRITERION = (
    "この単位の「未定義・要決定」がすべて決まり、設計に反映されている"
    "(手順書が「古い」なら再生成している)"
)

# AI 向けの版の依頼文と制約(作成方針16・17章)
AI_INTRO = "あなたは実装担当者です。以下の設計情報に従って実装してください。"
AI_CONSTRAINTS: tuple[str, ...] = ("既存の API を変更しない。", "設計に無いことを推測で決めない。")
AI_OUTRO = (
    "まず現在のリポジトリを調査し、既存実装との整合性を確認してください。"
    "不整合がある場合は実装せず、問題点を報告してください。"
)

FILE_KIND_LABELS: dict[UnitFileKind, str] = {
    "module": "モジュール",
    "test": "テスト",
    "config": "環境・設定",
}


def file_kind_label(kind: UnitFileKind) -> str:
    return FILE_KIND_LABELS[kind]


# ---------------------------------------------------------------------------
# index.md
# ---------------------------------------------------------------------------


def to_index_markdown(source: ProcedureOutputSource) -> str:
    """`implementation_procedure/index.md`の全文。"""
    lines = [f"# 実装手順書: {source.title}", ""]
    if not source.approved:
        lines.append(PROCEDURE_UNAPPROVED_TEXT)
        return "\n".join(lines) + "\n"
    lines += _overview(source)
    lines += _premises(source)
    lines += _unit_list(source)
    lines += _finding_list(source)
    lines += ["## 5. 完了条件", "", "各単位の完了条件(すべての単位に共通):", ""]
    lines += [f"- [ ] {c}" for c in (*COMPLETION_CRITERIA, DECIDED_CRITERION)]
    return "\n".join(lines).rstrip() + "\n"


def _overview(source: ProcedureOutputSource) -> list[str]:
    milestones = source.plan.milestones
    lines = ["## 1. 実装概要", "", "### 対象", ""]
    if milestones:
        lines += [f"{source.labels.plan}のマイルストーン {_milestone_span(source.plan)}。", ""]
    lines += md_table(
        ["M-ID", "名前", "優先度", "ゴール"],
        [[milestone_id(i), m.name, m.priority, m.goal] for i, m in enumerate(milestones)],
    )
    lines += ["", "### 対象外", ""]
    if source.out_of_scope:
        lines += ["要件定義書 1.4 の Should / Could / Won't(手順書を作らない)。", ""]
        lines += [f"- {text}" for text in source.out_of_scope]
    else:
        lines.append("—")
    return [*lines, ""]


def _premises(source: ProcedureOutputSource) -> list[str]:
    labels = source.labels
    lines = ["## 2. 実装前提・制約", "", f"### {labels.environment}", ""]
    lines += [source.environment.strip() or "—", "", f"### {labels.rules}", ""]
    lines += [f"- {rule}" for rule in source.rules] or ["—"]
    return [*lines, ""]


def _unit_list(source: ProcedureOutputSource) -> list[str]:
    rows = []
    for unit in source.units:
        procedure = source.procedures.get(unit.unit_id)
        name = f"{unit.unit_id} {unit.task.title}"
        if procedure is None:
            rows.append(
                [name, _kind(unit), _functions(unit), _depends(unit), NOT_GENERATED_TEXT, "", ""]
            )
            continue
        rows.append(
            [
                f"[{name}](./{unit_filename(unit)})",
                _kind(unit),
                _functions(unit),
                _depends(unit),
                "、".join(f"`{f.path}`" for f in procedure.files) or "—",
                " / ".join(procedure.verify),
                count_text(unit_findings(source, unit.unit_id)),
            ]
        )
    lines = ["## 3. 単位の一覧(依存順)", ""]
    lines += md_table(UNIT_LIST_HEADERS, rows)
    lines += [
        "",
        f"「{NOT_GENERATED_TEXT}」は、手順書をまだ生成していない単位(人が選んだ単位を、1回に5件まで"
        "生成する)。",
        "",
    ]
    return lines


def _finding_list(source: ProcedureOutputSource) -> list[str]:
    lines = [
        "## 4. 未定義・要決定の一覧(実装可能性チェックの結果)",
        "",
        f"{source.labels.fix_guide}手順書の上では決めない。",
        "",
        "### 全体(単位によらない)",
        "",
    ]
    overall = unit_findings(source, None)
    heading = source.labels.fix_heading
    lines += _finding_table(overall, heading, with_unit=False) if overall else ["なし"]
    lines += ["", "### 単位ごと", ""]
    per_unit = [f for f in source.findings if f.unit is not None]
    lines += _finding_table(per_unit, heading, with_unit=True) if per_unit else ["なし"]
    return [*lines, ""]


def _finding_table(
    findings: Sequence[UnitFinding], fix_heading: str, *, with_unit: bool
) -> list[str]:
    headers = ["重要度", "出どころ", *(["単位"] if with_unit else []), "対象", "内容", fix_heading]
    return md_table(
        headers,
        [
            [
                LEVEL_LABELS[f.level],
                ORIGIN_LABELS[f.origin],
                *([f.unit or ""] if with_unit else []),
                f.target,
                f.message,
                fix_target_text(f),
            ]
            for f in findings
        ],
    )


# ---------------------------------------------------------------------------
# 単位の md(人向け)
# ---------------------------------------------------------------------------


def to_unit_markdown(source: ProcedureOutputSource, unit_id: str) -> str:
    """単位1つの手順書の md(人向け)。手順書の無い単位は`KeyError`。"""
    unit, procedure, context = _parts(source, unit_id)
    lines = [f"# {unit.unit_id} {unit.task.title}", "", f"> {_unit_meta(source, unit)}", ""]
    lines += ["## 目的", "", procedure.purpose.strip() or "—", ""]
    lines += ["## 対象の処理・参照する設計", ""]
    lines += [f"- {_ref_line(ref)}" for ref in context.refs] or ["—"]
    lines.append("")
    for ref in context.refs:
        if ref.mermaid:
            lines += [
                f"### シーケンス図({ref.label} の手順から導出)",
                "",
                "```mermaid",
                ref.mermaid,
                "```",
                "",
            ]
    lines += ["## 作成・変更するファイル(依存順)", ""]
    lines += md_table(
        ["ファイル", "種類", "責務", "根拠"],
        [
            [f"`{f.path}`", file_kind_label(f.kind), f.responsibility, f.basis]
            for f in procedure.files
        ],
    )
    lines += ["", "## 実装の要点(設計に書いていないことだけ)", ""]
    lines += _bullets(procedure.notes)
    lines += ["", "## テスト観点", ""]
    lines += _test_lines(procedure)
    lines += ["", "## 確認方法", ""]
    lines += _bullets(procedure.verify)
    lines += ["", "## 未定義・要決定", ""]
    findings = unit_findings(source, unit.unit_id)
    heading = source.labels.fix_heading
    lines += _finding_table(findings, heading, with_unit=False) if findings else ["なし"]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# AI 向けの版
# ---------------------------------------------------------------------------


def to_ai_markdown(source: ProcedureOutputSource, unit_id: str) -> str:
    """単位1つの AI 向けの md(作成方針17章の形)。手順書の無い単位は`KeyError`。"""
    unit, procedure, context = _parts(source, unit_id)
    findings = unit_findings(source, unit.unit_id)
    lines = [*ai_warnings(source, findings), AI_INTRO, ""]
    lines += ["## プロジェクト概要", "", _project_line(source, unit), ""]
    lines += ["## 実装ルール", "", f"- {source.labels.module_rule}", ""]
    for section in (context.crosscutting, context.environment):
        if section:
            lines += [section, ""]
    lines += ["## 今回の実装単位", ""]
    functions = ", ".join(unit.task.function_ids) or "なし"
    lines += [f"{unit.unit_id} {unit.task.title}(種別: {_kind(unit)} / 処理: {functions})", ""]
    lines += [f"目的: {procedure.purpose.strip() or '—'}", ""]
    depends = _depends(unit)
    lines += [f"前提: {f'{depends} まで実装済み' if depends else 'なし'}", ""]
    lines += ["## 作成・変更するファイル(依存順)", ""]
    lines += md_table(
        ["ファイル", "種類", "責務"],
        [[f"`{f.path}`", file_kind_label(f.kind), f.responsibility] for f in procedure.files],
    )
    if procedure.notes:
        lines += ["", "実装の要点:", "", *_bullets(procedure.notes)]
    lines += ["", "## 参照する設計", ""]
    for ref in context.refs:
        lines += [ref.markdown if ref.markdown else f"- {ref.label}: 設計にありません", ""]
    if not context.refs:
        lines += ["—", ""]
    lines += ["## テスト観点", ""]
    lines += _test_lines(procedure)
    lines += ["", "## 完了条件", ""]
    lines += [f"- {c}" for c in COMPLETION_CRITERIA]
    lines += [f"- 確認方法: {v}" for v in procedure.verify if v.strip()]
    lines += ["", "## 未定義・要決定(決まるまで、推測で実装しないこと)", ""]
    lines += [
        f"- [{LEVEL_LABELS[f.level]}] {f'{f.target}: ' if f.target else ''}{f.message}"
        for f in findings
    ] or ["なし"]
    lines += ["", "## 制約", "", *[f"- {c}" for c in AI_CONSTRAINTS], "", AI_OUTRO]
    return "\n".join(lines).rstrip() + "\n"


def ai_warnings(source: ProcedureOutputSource, findings: Sequence[UnitFinding]) -> list[str]:
    """AI 向けの版の先頭の警告(段階8が承認済みでない・未定義が残る。無ければ空)。"""
    lines: list[str] = []
    if source.state == "outdated":
        lines.append(
            "> ⚠ 段階8(実装手順書)は古くなっています。手順書を作った後に設計が変わりました。"
            "作り直してから渡すことを勧めます。"
        )
    elif not source.approved:
        lines.append(
            "> ⚠ 段階8(実装手順書)は未承認です。人が確定していない下書きです。"
        )
    if findings:
        critical = count_by_level(findings)["critical"]
        lines.append(
            f"> ⚠ この単位には「未定義・要決定」が {len(findings)} 件残っています"
            f"(最重要 {critical} 件)。決めてから渡すことを勧めます。"
        )
    return [*lines, ""] if lines else []


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------


def _parts(
    source: ProcedureOutputSource, unit_id: str
) -> tuple[PlanUnit, UnitProcedure, UnitContext]:
    unit = source.unit(unit_id)
    if unit is None or unit_id not in source.procedures:
        raise KeyError(unit_id)
    return unit, source.procedures[unit_id], source.contexts[unit_id]


def _kind(unit: PlanUnit) -> str:
    return UNIT_KIND_LABELS[unit.task.kind]


def _functions(unit: PlanUnit) -> str:
    return ", ".join(unit.task.function_ids) or "—"


def _depends(unit: PlanUnit) -> str:
    return ", ".join(d.strip() for d in unit.task.depends_on if d.strip())


def _unit_meta(source: ProcedureOutputSource, unit: PlanUnit) -> str:
    return (
        f"種別: {_kind(unit)} / マイルストーン: {unit.milestone} {source.milestone_name(unit)}"
        f" / 依存: {_depends(unit) or 'なし'}"
    )


def _project_line(source: ProcedureOutputSource, unit: PlanUnit) -> str:
    return (
        f"{source.title}: {source.labels.plan}のマイルストーン {_milestone_span(source.plan)}"
        " を実装する。"
        "今回はそのうち、"
        f"{unit.milestone}({source.milestone_name(unit)})の単位 {unit.unit_id} だけを扱う。"
    )


def _milestone_span(plan: PlanModel) -> str:
    """マイルストーンの範囲(`M-01`・`M-01〜M-04`)。"""
    count = len(plan.milestones)
    return milestone_id(0) if count <= 1 else f"{milestone_id(0)}〜{milestone_id(count - 1)}"


def _ref_line(ref: ExpandedRef) -> str:
    return ref.label if ref.resolved else f"{ref.label}(設計に無い ── 未定義を参照)"


def _bullets(items: Sequence[str]) -> list[str]:
    return [f"- {item}" for item in items if item.strip()] or ["なし"]


def _test_lines(procedure: UnitProcedure) -> list[str]:
    lines = md_table(
        ["#", "観点", "SUT", "ドライバ", "スタブ"],
        [
            [f"TC-{i:02d}", t.viewpoint, t.sut, t.driver, t.stub]
            for i, t in enumerate(procedure.tests, start=1)
        ],
    )
    gwt = [f"- {g}" for g in procedure.gwt if g.strip()]
    return [*lines, "", *gwt] if gwt else lines
