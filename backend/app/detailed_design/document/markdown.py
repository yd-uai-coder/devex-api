"""詳細設計書の Markdown を組み立てる(純粋関数)。

docs/external_design.md 2.7節「詳細設計書の出力」。md は差分を取る・AI に読ませるための形で、
章の間のリンクと生の HTML を持たない。ID は本文に書くだけにする(「→ 詳細: L-02」
「呼ばれる手順: F-01#4」)。`<a id>`のアンカーは md のビューアで消えて飛べなかったため。

図は、zip の中の SVG を相対パスの画像で載せる(`![題](diagrams/x.svg)`)。md のビューアで
開くと図が見える。画像は章の間のリンクではない。

段階7の実装計画は、詳細設計書とは別の md(`to_plan_markdown`)にする(簡易モードで
実装計画書が別の文書なのとそろえる)。段階7の下書きの入力には、詳細設計書の md(`to_markdown`の
01〜06章)を使う。
"""

from collections.abc import Mapping, Sequence

from app.detailed_design.document.source import (
    CHAPTERS,
    Chapter,
    DocumentSource,
    RenderedDiagram,
)
from app.detailed_design.document.views import (
    UNIT_HEADERS,
    UNIT_KIND_LABELS,
    CrudMark,
    crud_matrix,
    data_item_usage,
    function_plans,
    functions_by_id,
    involvement,
    linked_logic_ids,
    logic_ids,
    logic_views,
    main_step_count,
    procedure_steps,
)
from app.detailed_design.logic import LogicRow
from app.detailed_design.plan import PLAN_STAGE, milestone_functions, milestone_id, task_id
from app.detailed_design.procedure import Procedure

UNAPPROVED_TEXT = "未承認(段階{stage}が承認されていません。承認すると、この章が組み立てられます)"
SKIPPED_TEXT = "省略(段階6を飛ばしました)"
PLAN_UNAPPROVED_TEXT = "未承認(段階7が承認されていません。承認すると、実装計画が組み立てられます)"

# CRUD 図の記号(md には色が無いので、決まり方を印で書き分ける)
_MARK_SUFFIX = {"dfd": "", "dfd_write": "+", "human": "*"}
CRUD_LEGEND = (
    "記号: 印なし = DFD の線から決まる R / `+` = 書き込みは DFD の線から決まり、"
    "C/U/D の区別は人が確定 / `*` = DFD に描いていない分で、人が確定"
)


def md_cell(text: str) -> str:
    """表のセルに入れる文字列(`|`と改行で表を崩さない形にする。空なら「—」)。改行は`<br>`に
    せず空白にする(md に生の HTML を書かないため)。"""
    value = " ".join(text.split()).replace("|", "\\|")
    return value or "—"


def md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Markdown の表の行(セルは`md_cell`で整える)。"""
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(md_cell(cell) for cell in row) + " |" for row in rows]
    return lines


def image(diagram: RenderedDiagram) -> str:
    return f"![{diagram.title}]({diagram.path})"


def to_markdown(source: DocumentSource, chapters: Sequence[Chapter] = CHAPTERS) -> str:
    """詳細設計書の md の全文(既定は01〜07章)。`chapters`で章を絞れる(段階7の下書きの入力は
    01〜06章だけ。07 は段階7自身が作るため)。"""
    lines = [f"# 詳細設計書: {source.title}", ""]
    for chapter in chapters:
        lines += [f"## {chapter.number} {chapter.title}", ""]
        lines += _chapter_body(source, chapter)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _chapter_body(source: DocumentSource, chapter: Chapter) -> list[str]:
    status = source.status(chapter.stage)
    if status == "unapproved":
        return [UNAPPROVED_TEXT.format(stage=chapter.stage)]
    if status == "skipped":
        return [SKIPPED_TEXT]
    return _BODIES[chapter.stage](source)


def _functions(source: DocumentSource) -> list[str]:
    assert source.function_list is not None
    rows = [
        [f.id, f.name, f.kind, f.trigger, ", ".join(f.screens), f.group, f.summary]
        for f in source.function_list.functions
    ]
    return md_table(
        ["処理ID", "名称", "種別", "トリガー", "関連画面", "機能グループ", "概要"], rows
    )


def _data_flow(source: DocumentSource) -> list[str]:
    assert source.data_flow is not None
    names = functions_by_id(source.function_list)
    lines: list[str] = []
    for group in source.data_flow.dfd_groups:
        lines += [f"### DFD: {group}", ""]
        diagram = source.dfd_diagrams.get(group)
        lines += [image(diagram) if diagram is not None else "(図がありません)", ""]
    usage = data_item_usage(source.dfd_models)
    lines += ["### データ辞書", ""]
    lines += md_table(
        ["データ項目", "フィールド", "使う処理"],
        [
            [item.name, ", ".join(item.fields), ", ".join(usage.get(item.id, []))]
            for item in source.data_items
        ],
    )
    lines += ["", "### 処理概要表(入力 / 処理 / 出力)", ""]
    lines += md_table(
        ["処理ID", "名称", "入力", "処理内容", "出力"],
        [
            [
                s.function_id,
                names[s.function_id].name if s.function_id in names else "",
                s.input,
                s.process,
                s.output,
            ]
            for s in source.data_flow.summaries
        ],
    )
    return lines


def _crud_cell(marks: Sequence[CrudMark] | None) -> str:
    if not marks:
        return ""
    return "".join(f"{m.op}{_MARK_SUFFIX[m.kind]}" for m in marks)


def _data_model(source: DocumentSource) -> list[str]:
    assert source.crud is not None
    lines: list[str] = []
    if source.er_diagram is not None:
        lines += [image(source.er_diagram), ""]
    lines += ["### テーブル定義", ""]
    for table in source.er.elements if source.er is not None else []:
        lines += [f"#### {table.name}", ""]
        if table.description.strip():
            lines += [table.description.strip(), ""]
        lines += md_table(
            ["列", "型", "キー", "NULL", "制約", "説明"],
            [
                [
                    c.name,
                    c.type,
                    "PK" if c.is_primary_key else ("FK" if c.is_foreign_key else ""),
                    "可" if c.nullable else "不可",
                    c.constraints,
                    c.description,
                ]
                for c in table.columns
            ],
        )
        lines.append("")
    names = functions_by_id(source.function_list)
    matrix = crud_matrix(source.crud, source.er, source.dfd_models, list(names))
    lines += ["### CRUD 図(処理 × テーブル)", ""]
    lines += md_table(
        ["処理ID", "名称", *matrix.tables],
        [
            [
                fid,
                names[fid].name if fid in names else "",
                *(_crud_cell(ops.get(t)) for t in matrix.tables),
            ]
            for fid, ops in matrix.rows
        ],
    )
    lines += ["", CRUD_LEGEND]
    return lines


def _structure(source: DocumentSource) -> list[str]:
    assert source.modules is not None
    lines: list[str] = []
    if source.component_diagram is not None:
        lines += [image(source.component_diagram), ""]
    lines += ["### モジュール一覧", ""]
    lines += md_table(
        ["パス", "層", "責務", "主な依存先", "関わる処理"],
        [
            [
                m.path,
                m.layer,
                m.responsibility,
                ", ".join(m.depends_on),
                "全処理" if m.all_functions else ", ".join(m.functions),
            ]
            for m in source.modules.modules
        ],
    )
    return lines


def procedure_table(procedure: Procedure, ids: Mapping[str, str]) -> list[str]:
    """1つの処理の手順の表(05 の本文。段階8の参照の展開も同じ表を使う)。"""
    return md_table(
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
        [
            [
                s.number,
                "" if s.step.is_branch else f"{s.step.caller} → {s.step.callee}",
                s.step.call + (f" → 詳細: {s.logic_id}" if s.logic_id else ""),
                s.step.data,
                s.step.action,
                s.step.result,
                s.step.db,
                s.step.branch,
            ]
            for s in procedure_steps(procedure, ids)
        ],
    )


def logic_spec(row: LogicRow) -> list[str]:
    """1つの関数のモジュール仕様の表と擬似フロー(06 の本文。段階8の参照の展開も同じ形を使う)。"""
    lines = md_table(
        ["項目", "内容"],
        [
            ["シグネチャ", row.signature],
            ["引数", row.args],
            ["戻り値", row.returns],
            ["例外", row.raises],
            ["事前条件", row.pre],
            ["事後条件", row.post],
        ],
    )
    if row.pseudo:
        lines += ["", "擬似フロー:", ""]
        for n, step in enumerate(row.pseudo, start=1):
            lines.append(f"{n}. {step.text}")
            lines += [f"    - {sub}" for sub in step.sub]
    return lines


def _procedures(source: DocumentSource) -> list[str]:
    assert source.procedures is not None
    names = functions_by_id(source.function_list)
    ids = logic_ids(source.logics)
    procedures = source.procedures.procedures
    lines = ["### 5.0 索引", ""]
    lines += md_table(
        ["処理ID", "名称", "トリガー", "選定理由", "手順数", "詳細(06)"],
        [
            [
                p.function_id,
                names[p.function_id].name if p.function_id in names else "",
                names[p.function_id].trigger if p.function_id in names else "",
                p.reason,
                str(main_step_count(p)),
                ", ".join(linked_logic_ids(procedure_steps(p, ids))),
            ]
            for p in procedures
        ],
    )
    table = involvement(source.procedures, source.modules)
    lines += ["", "### 5.0.1 処理 × モジュール(セルは手順番号)", ""]
    lines += md_table(
        ["処理ID", *table.modules],
        [
            [
                p.function_id,
                *(", ".join(table.cells[p.function_id].get(m, [])) for m in table.modules),
            ]
            for p in procedures
        ],
    )
    for i, p in enumerate(procedures, start=1):
        row = names.get(p.function_id)
        lines += ["", f"### 5.{i} {p.function_id} {row.name if row else ''}".rstrip(), ""]
        if row is not None and row.trigger:
            lines += [f"トリガー: {row.trigger}", ""]
        if p.reason.strip():
            lines += [f"選定理由: {p.reason.strip()}", ""]
        lines += procedure_table(p, ids)
        if p.note.strip():
            lines += ["", f"注記: {p.note.strip()}"]
    return lines


def _logics(source: DocumentSource) -> list[str]:
    views = logic_views(source.logics, source.procedures)
    lines = ["### 6.0 逆引き(関数 × 手順)", ""]
    lines += md_table(
        ["L-ID", "関数", "モジュール", "呼ばれる手順"],
        [[v.logic_id, v.row.function, v.row.module, ", ".join(v.step_ids)] for v in views],
    )
    for i, v in enumerate(views, start=1):
        row = v.row
        lines += ["", f"### 6.{i} {v.logic_id} {row.function}", ""]
        lines += [
            f"呼ばれる手順: {', '.join(v.step_ids) or '—'}",
            "",
            f"モジュール: {row.module}",
            "",
        ]
        lines += logic_spec(row)
    return lines


def _crosscutting(source: DocumentSource) -> list[str]:
    assert source.plan is not None
    return md_table(
        ["項目", "方針", "関わるファイル(例)"],
        [[row.topic, row.policy, ", ".join(row.modules)] for row in source.plan.crosscutting],
    )


_BODIES = {
    1: _functions,
    2: _data_flow,
    3: _data_model,
    4: _structure,
    5: _procedures,
    6: _logics,
    7: _crosscutting,
}


def to_plan_markdown(source: DocumentSource) -> str:
    """実装計画の md の全文(段階7。未承認なら「未承認」とだけ書く)。

    マイルストーン一覧 → マイルストーンごとの単位(タスク) → 処理の割り当て → 開発環境 → リスクの順。
    マイルストーンの処理は単位の処理から導く。横断事項は詳細設計書の07章に書くので、ここには
    書かない。"""
    lines = [f"# 実装計画書: {source.title}", ""]
    plan = source.plan
    if source.status(PLAN_STAGE) != "approved" or plan is None:
        lines.append(PLAN_UNAPPROVED_TEXT)
        return "\n".join(lines) + "\n"
    lines += ["## 1 マイルストーン", ""]
    lines += md_table(
        ["M-ID", "名前", "優先度", "ゴール", "処理"],
        [
            [milestone_id(i), m.name, m.priority, m.goal, ", ".join(milestone_functions(m))]
            for i, m in enumerate(plan.milestones)
        ],
    )
    for index, milestone in enumerate(plan.milestones):
        lines += ["", f"### {milestone_id(index)} {milestone.name}({milestone.priority})", ""]
        if milestone.goal:
            lines += [f"ゴール: {milestone.goal}", ""]
        lines += md_table(
            UNIT_HEADERS,
            [
                [
                    task_id(index, t_index),
                    UNIT_KIND_LABELS[t.kind],
                    t.title,
                    ", ".join(t.function_ids),
                    ", ".join(t.depends_on),
                    ", ".join(t.modules),
                    ", ".join(t.config_files),
                ]
                for t_index, t in enumerate(milestone.tasks)
            ],
        )
    lines += ["", "## 2 処理の割り当て", ""]
    lines += md_table(
        ["処理ID", "名称", "単位"],
        [
            [row.function_id, row.name, ", ".join(row.units) or "未計画"]
            for row in function_plans(plan, source.function_list)
        ],
    )
    lines += ["", "## 3 開発環境・事前準備", "", plan.environment or "—", ""]
    lines += ["## 4 想定リスクと対策", ""]
    lines += md_table(["リスク", "対策"], [[r.risk, r.mitigation] for r in plan.risks])
    return "\n".join(lines).rstrip() + "\n"
