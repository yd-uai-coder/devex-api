"""実装手順書の HTML(1枚)を組み立てる(純粋関数)。

docs/external_design.md 2.8節「出力」。`implementation_procedure/implementation_procedure.html`。

- 詳細設計書・実装計画と同じ自己完結の1ページ(`document.html.page`。CSS とスクリプトを中に持つ)。
- 単位はタブにせず、縦に並べる(実装計画の HTML と同じ形)。概要 → 実装前提 → 単位の一覧 →
  未定義の一覧 → 完了条件 → 単位ごとの手順書。単位の ID はアンカーを持ち、一覧・依存・未定義の
  単位の ID から移れる。単位が多くても、ページ内の検索と印刷で全単位を通して読める。
- 中身は md(`procedure_output/markdown.py`)と同じ。参照する設計は ID(見出し)だけを書き、
  段階5の手順のシーケンス図は SVG(`ExpandedRef.svg`)で埋め込む。
- 段階8が承認済みでなければ「未承認」とだけ書く。

文字はすべて`e`(`html.escape`)で書く。例外は図の SVG だけ(`document/html.py`の docstring 参照)。
"""

from collections.abc import Sequence

from app.detailed_design.document.html import badge, e, mono, page, table
from app.detailed_design.document.views import UNIT_KIND_LABELS, anchor
from app.detailed_design.plan import milestone_id
from app.detailed_design.procedure_doc import PlanUnit
from app.detailed_design.procedure_output.markdown import (
    COMPLETION_CRITERIA,
    DECIDED_CRITERION,
    MODULE_RULE,
    NOT_GENERATED_TEXT,
    UNIT_LIST_HEADERS,
    file_kind_label,
)
from app.detailed_design.procedure_output.source import (
    LEVEL_LABELS,
    ORIGIN_LABELS,
    ProcedureOutputSource,
    UnitFinding,
    count_text,
    fix_stage_text,
    unit_findings,
)

PROCEDURE_UNAPPROVED_HTML = (
    "未承認 ── 段階8が承認されていません。承認すると、実装手順書が組み立てられます。"
)

# 単位の手順書の枠(`.panel`はタブ用でスクリプトが隠すため使わない)
_UNIT_STYLE = (
    "display:grid;gap:10px;padding:14px;border:1px solid var(--rule);"
    "background:var(--sheet);scroll-margin-top:12px;min-width:0"
)


def to_procedure_html(source: ProcedureOutputSource) -> str:
    """実装手順書の HTML の全文。"""
    title = f"実装手順書: {source.title}"
    sections = [
        ("1", "実装概要"),
        ("2", "実装前提・制約"),
        ("3", "単位の一覧(依存順)"),
        ("4", "未定義・要決定の一覧"),
        ("5", "完了条件"),
        ("6", "単位ごとの手順書"),
    ]
    toc = "".join(f'<a href="#proc{n}">{e(f"{n} {h}")}</a>' for n, h in sections)
    header = (
        '<header style="display:grid;gap:12px"><div class="muted">Devex ／ 実装手順書</div>'
        f"<h1>{e(source.title)}</h1>"
        '<p class="muted">承認済みの段階8と段階1〜7から組み立てた実装手順書です。青い単位の ID を'
        "押すと、その単位の手順書へ移ります。</p>"
        + (f'<nav class="toc">{toc}</nav>' if source.approved else "")
        + "</header>"
    )
    if not source.approved:
        return page(title, header + f'<p class="status">{e(PROCEDURE_UNAPPROVED_HTML)}</p>')
    bodies = [
        _overview(source),
        _premises(source),
        _unit_list(source),
        _findings(source),
        _completion(),
        _units(source),
    ]
    return page(
        title,
        header + "".join(_section(n, h, b) for (n, h), b in zip(sections, bodies, strict=True)),
    )


def _section(number: str, heading: str, body: str) -> str:
    return (
        f'<section class="chapter" id="proc{number}"><div class="rail">'
        f'<div class="no">{e(number)}</div></div>'
        f'<div class="body"><h2>{e(heading)}</h2>{body}</div></section>'
    )


def _overview(source: ProcedureOutputSource) -> str:
    milestones = table(
        ["M-ID", "名前", "優先度", "ゴール"],
        [
            [mono(milestone_id(i)), e(m.name), e(m.priority), e(m.goal)]
            for i, m in enumerate(source.plan.milestones)
        ],
        label="対象のマイルストーン",
    )
    if source.out_of_scope:
        out = (
            "<p>要件定義書 1.4 の Should / Could / Won't(手順書を作らない)。</p>"
            + _list(source.out_of_scope)
        )
    else:
        out = '<p class="muted">—</p>'
    return f"<h3>対象</h3>{milestones}<h3>対象外</h3>{out}"


def _premises(source: ProcedureOutputSource) -> str:
    environment = source.plan.environment.strip()
    stack = (
        f'<p style="white-space:pre-wrap">{e(environment)}</p>'
        if environment
        else '<p class="muted">—</p>'
    )
    rules = [MODULE_RULE] + [f"07章 {r.topic}: {r.policy}" for r in source.plan.crosscutting]
    return (
        f"<h3>技術スタック・開発環境(段階7)</h3>{stack}"
        f"<h3>実装ルール(段階4・07章より)</h3>{_list(rules)}"
    )


def _unit_list(source: ProcedureOutputSource) -> str:
    rows = []
    for unit in source.units:
        procedure = source.procedures.get(unit.unit_id)
        if procedure is None:
            rows.append(
                [
                    f"{mono(unit.unit_id)} {e(unit.task.title)}",
                    e(UNIT_KIND_LABELS[unit.task.kind]),
                    e(", ".join(unit.task.function_ids) or "—"),
                    _depends(source, unit),
                    f'<span class="muted">{e(NOT_GENERATED_TEXT)}</span>',
                    "",
                    "",
                ]
            )
            continue
        rows.append(
            [
                f"{badge(unit.unit_id)} {e(unit.task.title)}",
                e(UNIT_KIND_LABELS[unit.task.kind]),
                e(", ".join(unit.task.function_ids) or "—"),
                _depends(source, unit),
                "<br>".join(mono(f.path) for f in procedure.files) or "—",
                e(" / ".join(procedure.verify)),
                e(count_text(unit_findings(source, unit.unit_id))),
            ]
        )
    note = (
        f'<p class="muted">「{e(NOT_GENERATED_TEXT)}」は、手順書をまだ生成していない単位'
        "(人が選んだ単位を、1回に5件まで生成する)。</p>"
    )
    return table(UNIT_LIST_HEADERS, rows, label="単位の一覧") + note


def _findings(source: ProcedureOutputSource) -> str:
    overall = unit_findings(source, None)
    per_unit = [f for f in source.findings if f.unit is not None]
    none = '<p class="muted">なし</p>'
    return (
        "<p>「直す段階」へ戻って設計を直す。直した段階は差し戻され、この手順書は「古い」になる。"
        "手順書の上では決めない。</p>"
        "<h3>全体(単位によらない)</h3>"
        + (_finding_table(source, overall, with_unit=False) if overall else none)
        + "<h3>単位ごと</h3>"
        + (_finding_table(source, per_unit, with_unit=True) if per_unit else none)
    )


def _finding_table(
    source: ProcedureOutputSource, findings: Sequence[UnitFinding], *, with_unit: bool
) -> str:
    headers = ["重要度", "出どころ", *(["単位"] if with_unit else []), "対象", "内容", "直す段階"]
    rows = [
        [
            e(LEVEL_LABELS[f.level]),
            e(ORIGIN_LABELS[f.origin]),
            *([_unit_ref(source, f.unit or "")] if with_unit else []),
            e(f.target),
            e(f.message),
            e(fix_stage_text(f.fix_stage)),
        ]
        for f in findings
    ]
    return table(headers, rows, label="未定義・要決定")


def _completion() -> str:
    return "<p>各単位の完了条件(すべての単位に共通):</p>" + _list(
        [*COMPLETION_CRITERIA, DECIDED_CRITERION]
    )


def _units(source: ProcedureOutputSource) -> str:
    if not source.procedures:
        return '<p class="muted">手順書を生成した単位がありません。</p>'
    return "".join(_unit(source, u) for u in source.units if u.unit_id in source.procedures)


def _unit(source: ProcedureOutputSource, unit: PlanUnit) -> str:
    procedure = source.procedures[unit.unit_id]
    context = source.contexts[unit.unit_id]
    meta = (
        f"種別: {e(UNIT_KIND_LABELS[unit.task.kind])} / マイルストーン: "
        f"{e(unit.milestone)} {e(source.milestone_name(unit))} / 依存: "
        + (_depends(source, unit) or "なし")
    )
    refs = [
        e(_plain(ref.label))
        if ref.resolved
        else f"{e(_plain(ref.label))}(設計に無い ── 未定義を参照)"
        for ref in context.refs
    ]
    figures = "".join(
        f'<p class="muted">シーケンス図({e(_plain(ref.label))} の手順から導出)</p>'
        f'<div class="figure" role="img" aria-label="{e(_plain(ref.label))} のシーケンス図">'
        f"{ref.svg}</div>"
        for ref in context.refs
        if ref.svg
    )
    files = table(
        ["ファイル", "種類", "責務", "根拠"],
        [
            [mono(f.path), e(file_kind_label(f.kind)), e(f.responsibility), e(f.basis)]
            for f in procedure.files
        ],
        label=f"{unit.unit_id} のファイル",
    )
    tests = table(
        ["#", "観点", "SUT", "ドライバ", "スタブ"],
        [
            [mono(f"TC-{i:02d}"), e(t.viewpoint), e(t.sut), e(t.driver), e(t.stub)]
            for i, t in enumerate(procedure.tests, start=1)
        ],
        label=f"{unit.unit_id} のテスト観点",
    )
    findings = unit_findings(source, unit.unit_id)
    return (
        f'<article id="{e(anchor(unit.unit_id))}" style="{_UNIT_STYLE}">'
        f"<h3>{e(unit.unit_id)} {e(unit.task.title)}</h3><p class=\"muted\">{meta}</p>"
        f"<h4>目的</h4><p>{e(procedure.purpose.strip() or '—')}</p>"
        f"<h4>対象の処理・参照する設計</h4>{_list(refs, escaped=True)}{figures}"
        f"<h4>作成・変更するファイル(依存順)</h4>{files}"
        f"<h4>実装の要点(設計に書いていないことだけ)</h4>{_list(procedure.notes)}"
        f"<h4>テスト観点</h4>{tests}{_list(procedure.gwt) if procedure.gwt else ''}"
        f"<h4>確認方法</h4>{_list(procedure.verify)}"
        "<h4>未定義・要決定</h4>"
        + (
            _finding_table(source, findings, with_unit=False)
            if findings
            else '<p class="muted">なし</p>'
        )
        + "</article>"
    )


def _unit_ref(source: ProcedureOutputSource, unit_id: str) -> str:
    """単位の ID。手順書のある単位はその手順書へ移るバッジ、無ければ文字だけ。"""
    if not unit_id:
        return ""
    return badge(unit_id) if unit_id in source.procedures else mono(unit_id)


def _depends(source: ProcedureOutputSource, unit: PlanUnit) -> str:
    return " ".join(_unit_ref(source, d.strip()) for d in unit.task.depends_on if d.strip())


def _plain(label: str) -> str:
    """md 用の参照の見出し(パスを`で囲む)から、`を除く。"""
    return label.replace("`", "")


def _list(items: Sequence[str], *, escaped: bool = False) -> str:
    """箇条書き(空なら「なし」)。`escaped`ならエスケープ済みの HTML として入れる。"""
    values = [item if escaped else e(item) for item in items if item.strip()]
    if not values:
        return '<p class="muted">なし</p>'
    return "<ul>" + "".join(f"<li>{v}</li>" for v in values) + "</ul>"
