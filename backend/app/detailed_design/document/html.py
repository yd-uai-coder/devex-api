"""詳細設計書の HTML を組み立てる(純粋関数)。

docs/external_design.md 2.7節「詳細設計書の出力」。HTML は読むための形で、次の性質を持つ:

- 自己完結の単一ファイル。CSS とスクリプトを中に持ち、外部を読み込まない。図は SVG を中に入れる。
- レビュー画面(段階5・6)と同じタブと双方向のリンクを持つ。05 の手順の行 → 06 の詳細、06 の
  「呼ばれる手順」→ 05 の行、01 の処理ID → 05 の処理、05 の関与表のセル → 手順の行。
- スクリプトが無効でも、全件を並べて表示し、アンカーで飛べる(タブを隠すのはスクリプトが
  `js`のクラスを付けたときだけ)。

文字はすべて`html.escape`で書く。例外は図の SVG だけで、これはそのまま埋め込む。SVG は自前の
出力エンジン(app/uml/export/svg.py)が書いたもので、要素の名前などの文字はエンジンの中で
エスケープ済みのため(ここでもう一度エスケープすると、図ではなく SVG の文字列が表示される)。
"""

from collections.abc import Sequence
from html import escape

from app.detailed_design.document.source import (
    CHAPTERS,
    Chapter,
    DocumentSource,
    RenderedDiagram,
)
from app.detailed_design.document.views import (
    CrudMark,
    anchor,
    crud_matrix,
    data_item_usage,
    functions_by_id,
    involvement,
    linked_logic_ids,
    logic_ids,
    logic_views,
    main_step_count,
    procedure_steps,
)
from app.detailed_design.procedure import step_id

UNAPPROVED_TEXT = (
    "未承認 ── 段階{stage}が承認されていません。承認すると、この章が組み立てられます。"
)
SKIPPED_TEXT = "省略 ── 段階6を飛ばしました。"

STYLE = """
:root { --paper:#f5f6f8; --sheet:#fff; --ink:#1d2433; --muted:#5b6475; --rule:#d6dbe4;
  --head:#eef1f6;
  --accent:#1f5fae; --accent-soft:#e4edf9; --warn:#9a5b00; --warn-soft:#fbf1e0;
  --branch:#f6f1e7; --hl:#fdf3c4;
  color-scheme: light; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --paper:#151a23;
  --sheet:#1c2230;
  --ink:#e3e7ee; --muted:#9aa3b4; --rule:#2e3647; --head:#242c3c; --accent:#7fb0f0;
  --accent-soft:#1f3150;
  --warn:#f0b45c; --warn-soft:#3a2c14; --branch:#2a2618; --hl:#4a4215; color-scheme: dark; } }
:root[data-theme="dark"] { --paper:#151a23; --sheet:#1c2230; --ink:#e3e7ee; --muted:#9aa3b4;
  --rule:#2e3647;
  --head:#242c3c; --accent:#7fb0f0; --accent-soft:#1f3150; --warn:#f0b45c; --warn-soft:#3a2c14;
  --branch:#2a2618;
  --hl:#4a4215; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; padding-block:28px 72px; padding-inline:16px; background:var(--paper);
  color:var(--ink);
  font:14.5px/1.75 "BIZ UDPGothic","Hiragino Kaku Gothic ProN","Yu Gothic","Meiryo",sans-serif; }
.wrap { max-width:1120px; margin:0 auto; display:grid; gap:40px; }
h1 { font-size:1.7rem; margin:0; line-height:1.35; } h2 { font-size:1.3rem; margin:0; }
h3 { font-size:1.02rem; margin:10px 0 0; } h4 { font-size:0.95rem; margin:6px 0 0; }
p { margin:0; max-width:78ch; } .muted { color:var(--muted); font-size:0.88em; }
.mono { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:0.88em; }
.status { justify-self:start; padding:6px 12px; border-radius:4px; color:var(--warn);
  background:var(--warn-soft); }
nav.toc { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:0.9em; }
nav.toc a { color:var(--accent); text-decoration:none; }
section.chapter { display:grid; grid-template-columns:84px minmax(0,1fr); gap:22px;
  scroll-margin-top:12px; }
.rail { border-right:2px solid var(--rule); padding-right:14px; text-align:right; }
.rail .no { font-family:ui-monospace,Consolas,monospace; font-size:1.55rem; color:var(--accent);
  line-height:1; }
.rail .step { font-size:0.74rem; color:var(--muted); margin-top:6px; }
.body { display:grid; gap:14px; min-width:0; }
.scroll { overflow-x:auto; border:1px solid var(--rule); background:var(--sheet); }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { border-bottom:1px solid var(--rule); padding:5px 9px; text-align:left;
  vertical-align:top; }
th { background:var(--head); white-space:nowrap; }
td.c { text-align:center; white-space:nowrap; }
tr.branch td { background:var(--branch); } tr.branch td:first-child { font-weight:700; }
.hl, tr.hl td { background:var(--hl) !important; }
.figure { background:#fff; border:1px solid var(--rule); padding:10px; overflow-x:auto; }
.figure svg { display:block; max-width:none; height:auto; }
a.badge { display:inline-block; padding:0 7px; border-radius:4px; background:var(--accent-soft);
  color:var(--accent);
  text-decoration:none; font-family:ui-monospace,Consolas,monospace; font-size:12px;
  margin:2px 2px 0 0; white-space:nowrap; }
a.badge:hover { text-decoration:underline; }
.op-dfd { color:var(--accent); font-weight:700; }
.op-dfd_write { color:var(--warn); font-weight:700; text-decoration:underline;
  text-decoration-color:var(--accent); }
.op-human { color:var(--warn); font-weight:700; }
.legend { display:flex; flex-wrap:wrap; gap:6px 18px; font-size:0.84em; color:var(--muted); }
.tabs { display:flex; flex-wrap:wrap; gap:6px; }
.tabs a { padding:3px 10px; border:1px solid var(--rule); border-radius:4px;
  background:var(--sheet); color:var(--ink);
  text-decoration:none; font-size:13px; }
.tabs a[aria-selected="true"] { background:var(--accent); border-color:var(--accent);
  color:#fff; }
.panel { display:grid; gap:8px; padding:12px; border:1px solid var(--rule);
  background:var(--sheet);
  scroll-margin-top:12px; min-width:0; }
.js .panel:not(.active) { display:none; }
ol { margin:0; padding-left:22px; } ol ul { margin:0; padding-left:18px; }
@media (max-width:640px) { section.chapter { grid-template-columns:1fr; gap:8px; }
  .rail { border-right:none; border-bottom:2px solid var(--rule); text-align:left; display:flex;
  gap:12px;
    align-items:baseline; padding:0 0 6px; } }
"""

# タブの切り替えと、アンカーへの移動。移動先がタブの中なら、そのタブを開いてから強調する。
# 同じアンカーをもう一度押したとき(hashchange が起きない)も移動し直す。
SCRIPT = """
(function () {
  document.documentElement.classList.add("js");
  function show(group, id) {
    document.querySelectorAll('.panel[data-group="' + group + '"]').forEach(function (p) {
      p.classList.toggle("active", p.id === id);
    });
    document.querySelectorAll('.tabs[data-group="' + group + '"] a').forEach(function (a) {
      a.setAttribute("aria-selected", a.getAttribute("href") === "#" + id ? "true" : "false");
    });
  }
  function go() {
    var id = decodeURIComponent(location.hash.slice(1));
    var el = id && document.getElementById(id);
    if (!el) return;
    var panel = el.closest(".panel");
    if (panel) show(panel.dataset.group, panel.id);
    document.querySelectorAll(".hl").forEach(function (x) { x.classList.remove("hl"); });
    var whole = el === panel || el.matches("section");
    if (!whole) el.classList.add("hl");
    el.scrollIntoView({ block: whole ? "start" : "center" });
  }
  document.querySelectorAll(".tabs[data-group]").forEach(function (t) {
    var first = document.querySelector('.panel[data-group="' + t.dataset.group + '"]');
    if (first) show(t.dataset.group, first.id);
  });
  document.addEventListener("click", function (ev) {
    var a = ev.target.closest && ev.target.closest('a[href^="#"]');
    if (a && a.getAttribute("href") === location.hash) { ev.preventDefault(); go(); }
  });
  window.addEventListener("hashchange", go);
  go();
})();
"""

_CRUD_LEGEND = (
    '<div class="legend">'
    '<span><span class="op-dfd">R</span> DFD の線(ストア → 処理)から決まる</span>'
    '<span><span class="op-dfd_write">C U D</span> 書き込みは DFD の線(処理 → ストア)から決まり、'
    "C/U/D の区別は人が確定</span>"
    '<span><span class="op-human">R C U D</span> DFD に描いていない分で、人が確定</span></div>'
)


def e(text: str) -> str:
    return escape(text, quote=True)


def badge(identifier: str, label: str | None = None) -> str:
    """ID へ移るバッジ(リンク)。"""
    return f'<a class="badge" href="#{e(anchor(identifier))}">{e(label or identifier)}</a>'


def mono(text: str) -> str:
    return f'<span class="mono">{e(text)}</span>' if text else ""


def table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    label: str = "",
    row_attrs: Sequence[str] | None = None,
) -> str:
    """表。セルは呼び出し側でエスケープ済みの HTML を渡す(バッジを入れるため)。"""
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    attrs = row_attrs or [""] * len(rows)
    body = "".join(
        f"<tr{attr}>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row, attr in zip(rows, attrs, strict=True)
    )
    aria = f' aria-label="{e(label)}"' if label else ""
    return (
        f'<div class="scroll"><table{aria}><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def tabs(group: str, items: Sequence[tuple[str, str]]) -> str:
    """タブの見出し(`items`は(アンカー, 表示名))。"""
    links = "".join(f'<a href="#{e(a)}">{e(label)}</a>' for a, label in items)
    return f'<nav class="tabs" data-group="{e(group)}">{links}</nav>'


def panel(group: str, panel_id: str, body: str) -> str:
    return f'<section class="panel" data-group="{e(group)}" id="{e(panel_id)}">{body}</section>'


def figure(diagram: RenderedDiagram | None) -> str:
    """図(SVG をそのまま埋め込む。モジュールの docstring 参照)。"""
    if diagram is None:
        return '<p class="muted">(図がありません)</p>'
    return f'<div class="figure" role="img" aria-label="{e(diagram.title)}">{diagram.svg}</div>'


def to_html(source: DocumentSource) -> str:
    """詳細設計書の HTML の全文(01〜06章)。"""
    toc = "".join(f'<a href="#ch{c.number}">{e(f"{c.number} {c.title}")}</a>' for c in CHAPTERS)
    header = (
        '<header style="display:grid;gap:12px"><div class="muted">Devex ／ 詳細設計書</div>'
        f"<h1>{e(source.title)}</h1>"
        '<p class="muted">承認済みの段階から組み立てた詳細設計書です。青い ID を押すと、'
        "該当する処理・手順・関数へ移ります。</p>"
        f'<nav class="toc">{toc}</nav></header>'
    )
    chapters = "".join(_chapter(source, c) for c in CHAPTERS)
    return (
        '<!doctype html>\n<html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(f'詳細設計書: {source.title}')}</title><style>{STYLE}</style></head>"
        f'<body><div class="wrap">{header}{chapters}</div><script>{SCRIPT}</script></body></html>\n'
    )


def _chapter(source: DocumentSource, chapter: Chapter) -> str:
    status = source.status(chapter.stage)
    if status == "unapproved":
        body = f'<p class="status">{e(UNAPPROVED_TEXT.format(stage=chapter.stage))}</p>'
    elif status == "skipped":
        body = f'<p class="status">{e(SKIPPED_TEXT)}</p>'
    else:
        body = _BODIES[chapter.stage](source)
    return (
        f'<section class="chapter" id="ch{chapter.number}"><div class="rail">'
        f'<div class="no">{e(chapter.number)}</div>'
        f'<div class="step">段階{chapter.stage}</div></div>'
        f'<div class="body"><h2>{e(chapter.title)}</h2>{body}</div></section>'
    )


def _functions(source: DocumentSource) -> str:
    assert source.function_list is not None
    with_procedure = (
        {p.function_id for p in source.procedures.procedures} if source.procedures else set()
    )
    rows = [
        [
            badge(f.id) if f.id in with_procedure else e(f.id),
            e(f.name),
            e(f.kind),
            mono(f.trigger),
            e(", ".join(f.screens)),
            e(f.group),
            e(f.summary),
        ]
        for f in source.function_list.functions
    ]
    note = '<p class="muted">青い処理ID は、05 に手順がある処理。</p>' if with_procedure else ""
    return note + table(
        ["処理ID", "名称", "種別", "トリガー", "関連画面", "機能グループ", "概要"],
        rows,
        label="機能一覧",
    )


def _data_flow(source: DocumentSource) -> str:
    assert source.data_flow is not None
    names = functions_by_id(source.function_list)
    groups = source.data_flow.dfd_groups
    parts: list[str] = []
    if groups:
        items = [(f"dfd-{i}", group) for i, group in enumerate(groups, start=1)]
        parts.append(tabs("dfd", items))
        parts += [
            panel("dfd", key, f"<h3>{e(group)}</h3>{figure(source.dfd_diagrams.get(group))}")
            for key, group in items
        ]
    usage = data_item_usage(source.dfd_models)
    parts.append("<h3>データ辞書</h3>")
    parts.append(
        table(
            ["データ項目", "フィールド", "使う処理"],
            [
                [e(item.name), mono(", ".join(item.fields)), e(", ".join(usage.get(item.id, [])))]
                for item in source.data_items
            ],
            label="データ辞書",
        )
    )
    parts.append("<h3>処理概要表(入力 / 処理 / 出力)</h3>")
    parts.append(
        table(
            ["処理ID", "名称", "入力", "処理内容", "出力"],
            [
                [
                    e(s.function_id),
                    e(names[s.function_id].name if s.function_id in names else ""),
                    e(s.input),
                    e(s.process),
                    e(s.output),
                ]
                for s in source.data_flow.summaries
            ],
            label="処理概要表",
        )
    )
    return "".join(parts)


def _crud_cell(marks: Sequence[CrudMark] | None) -> str:
    if not marks:
        return "—"
    return "".join(f'<span class="op-{m.kind}">{e(m.op)}</span>' for m in marks)


def _data_model(source: DocumentSource) -> str:
    assert source.crud is not None
    parts = [figure(source.er_diagram), "<h3>テーブル定義</h3>"]
    for t in source.er.elements if source.er is not None else []:
        parts.append(f"<h4>{mono(t.name)}</h4>")
        if t.description.strip():
            parts.append(f'<p class="muted">{e(t.description.strip())}</p>')
        parts.append(
            table(
                ["列", "型", "キー", "NULL", "制約", "説明"],
                [
                    [
                        mono(c.name),
                        mono(c.type),
                        "PK" if c.is_primary_key else ("FK" if c.is_foreign_key else ""),
                        "可" if c.nullable else "不可",
                        e(c.constraints),
                        e(c.description),
                    ]
                    for c in t.columns
                ],
                label=f"テーブル定義 {t.name}",
            )
        )
    names = functions_by_id(source.function_list)
    matrix = crud_matrix(source.crud, source.er, source.dfd_models, list(names))
    head = "<th>処理ID</th><th>名称</th>" + "".join(
        f'<th class="mono">{e(t)}</th>' for t in matrix.tables
    )
    body = "".join(
        f'<tr><td class="mono">{e(fid)}</td><td>{e(names[fid].name if fid in names else "")}</td>'
        + "".join(f'<td class="c">{_crud_cell(ops.get(t))}</td>' for t in matrix.tables)
        + "</tr>"
        for fid, ops in matrix.rows
    )
    parts.append("<h3>CRUD 図(処理 × テーブル)</h3>")
    parts.append(
        f'<div class="scroll"><table aria-label="CRUD 図"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )
    parts.append(_CRUD_LEGEND)
    return "".join(parts)


def _structure(source: DocumentSource) -> str:
    assert source.modules is not None
    rows = [
        [
            mono(m.path),
            e(m.layer),
            e(m.responsibility),
            mono(", ".join(m.depends_on)),
            "全処理" if m.all_functions else e(", ".join(m.functions)),
        ]
        for m in source.modules.modules
    ]
    return (
        figure(source.component_diagram)
        + "<h3>モジュール一覧</h3>"
        + table(["パス", "層", "責務", "主な依存先", "関わる処理"], rows, label="モジュール一覧")
    )


def _procedures(source: DocumentSource) -> str:
    assert source.procedures is not None
    names = functions_by_id(source.function_list)
    ids = logic_ids(source.logics)
    procedures = source.procedures.procedures
    steps = {p.function_id: procedure_steps(p, ids) for p in procedures}

    index_rows = [
        [
            badge(p.function_id),
            e(names[p.function_id].name if p.function_id in names else ""),
            mono(names[p.function_id].trigger if p.function_id in names else ""),
            e(p.reason),
            str(main_step_count(p)),
            "".join(badge(lid) for lid in linked_logic_ids(steps[p.function_id])) or "—",
        ]
        for p in procedures
    ]
    inv = involvement(source.procedures, source.modules)
    inv_head = "<th>処理ID</th>" + "".join(f'<th class="mono">{e(m)}</th>' for m in inv.modules)
    inv_body = "".join(
        f"<tr><td>{badge(p.function_id)}</td>"
        + "".join(
            '<td class="c">'
            + (
                "".join(
                    badge(step_id(p.function_id, n), n) for n in inv.cells[p.function_id].get(m, [])
                )
                or "—"
            )
            + "</td>"
            for m in inv.modules
        )
        + "</tr>"
        for p in procedures
    )
    parts = [
        "<h3>5.0 索引</h3>",
        table(
            ["処理ID", "名称", "トリガー", "選定理由", "手順数", "詳細(06)"],
            index_rows,
            label="手順の索引",
        ),
        "<h3>5.0.1 処理 × モジュール(セルは手順番号)</h3>",
        f'<div class="scroll"><table aria-label="関与表"><thead><tr>{inv_head}</tr></thead>'
        f"<tbody>{inv_body}</tbody></table></div>",
        tabs("proc", [(anchor(p.function_id), p.function_id) for p in procedures]),
    ]
    for i, p in enumerate(procedures, start=1):
        row = names.get(p.function_id)
        heading = f"5.{i} {p.function_id} {row.name if row else ''}".rstrip()
        meta = ""
        if row is not None and row.trigger:
            meta += f'<p class="muted">トリガー: {mono(row.trigger)}</p>'
        if p.reason.strip():
            meta += f'<p class="muted">選定理由: {e(p.reason.strip())}</p>'
        step_rows = [
            [
                e(s.number),
                "" if s.step.is_branch else f"{e(s.step.caller)} → {mono(s.step.callee)}",
                mono(s.step.call)
                + (f" {badge(s.logic_id, f'詳細 {s.logic_id} ↓')}" if s.logic_id else ""),
                e(s.step.data),
                e(s.step.action),
                e(s.step.result),
                e(s.step.db),
                e(s.step.branch),
            ]
            for s in steps[p.function_id]
        ]
        attrs = [
            f' id="{e(anchor(s.step_id))}"' + (' class="branch"' if s.step.is_branch else "")
            for s in steps[p.function_id]
        ]
        note = f'<p class="muted">注記: {e(p.note.strip())}</p>' if p.note.strip() else ""
        parts.append(
            panel(
                "proc",
                anchor(p.function_id),
                f"<h3>{e(heading)}</h3>{meta}"
                + table(
                    [
                        "No",
                        "呼び出し元 → 呼び出し先",
                        "関数",
                        "渡すデータ",
                        "処理内容",
                        "結果",
                        "DB 操作",
                        "分岐・例外",
                    ],
                    step_rows,
                    label=f"{p.function_id} の手順",
                    row_attrs=attrs,
                )
                + note,
            )
        )
    return "".join(parts)


def _logics(source: DocumentSource) -> str:
    views = logic_views(source.logics, source.procedures)
    reverse = [
        [
            badge(v.logic_id),
            mono(v.row.function),
            mono(v.row.module),
            "".join(badge(sid) for sid in v.step_ids) or "—",
        ]
        for v in views
    ]
    parts = [
        "<h3>6.0 逆引き(関数 × 手順)</h3>",
        table(["L-ID", "関数", "モジュール", "呼ばれる手順"], reverse, label="逆引き"),
        tabs("logic", [(anchor(v.logic_id), f"{v.logic_id} {v.row.function}") for v in views]),
    ]
    for i, v in enumerate(views, start=1):
        row = v.row
        called = "".join(badge(sid, f"↑ {sid}") for sid in v.step_ids) or "—"
        spec = table(
            ["項目", "内容"],
            [
                ["シグネチャ", mono(row.signature)],
                ["引数", e(row.args)],
                ["戻り値", e(row.returns)],
                ["例外", e(row.raises)],
                ["事前条件", e(row.pre)],
                ["事後条件", e(row.post)],
            ],
            label=f"{v.logic_id} の仕様",
        )
        pseudo = ""
        if row.pseudo:
            items = "".join(
                f"<li>{e(step.text)}"
                + (
                    "<ul>" + "".join(f"<li>{e(sub)}</li>" for sub in step.sub) + "</ul>"
                    if step.sub
                    else ""
                )
                + "</li>"
                for step in row.pseudo
            )
            pseudo = f"<h4>擬似フロー</h4><ol>{items}</ol>"
        parts.append(
            panel(
                "logic",
                anchor(v.logic_id),
                f"<h3>{e(f'6.{i} {v.logic_id} {row.function}')}</h3>"
                f'<p class="muted">呼ばれる手順: {called}</p>'
                f'<p class="muted">モジュール: {mono(row.module)}</p>' + spec + pseudo,
            )
        )
    return "".join(parts)


_BODIES = {
    1: _functions,
    2: _data_flow,
    3: _data_model,
    4: _structure,
    5: _procedures,
    6: _logics,
}
