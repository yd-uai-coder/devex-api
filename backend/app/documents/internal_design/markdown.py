"""内部設計書の model から md を組み立てる(純粋関数)。

見出しと表の列は、model にする前の内部設計書のプロンプトが決めていた形のまま(3.1〜3.4・
`### テーブル: <名前>`・`### モジュール一覧`・`#### DF-<n>: <トリガー>`・`- データ項目:`)。
model の無い古い版を読む解析(`app.detailed_design.simple_procedure.parse_internal_design`)が、
この md も同じように読める。

本文の節に AI が見出し(`## …`)を書くと、節の切り出し(`extract_section`)が途中で切れるので、
コードブロックの外の見出しは太字の行にする。
"""

import re

from app.detailed_design.document.markdown import md_table
from app.documents.internal_design.model import DataFlowRow, InternalDesignModel, TableDef

TITLE = "# 3. 内部設計書"
ARCHITECTURE_HEADING = "## 3.1 技術スタック選定・アーキテクチャ方針"
DATA_MODEL_HEADING = "## 3.2 データモデル定義"
MODULE_HEADING = "## 3.3 バックエンド処理・モジュール設計"
ERROR_POLICY_HEADING = "## 3.4 例外処理・エラーハンドリング・ログ設計"
_NONE_ROW = "(なし)"
_FENCE = "```"
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")


def body_text(text: str) -> str:
    """本文の節の文字列。コードブロックの外の見出しの行を太字の行にする。空なら「—」。"""
    if not text.strip():
        return "—"
    lines: list[str] = []
    in_fence = False
    for line in text.strip().splitlines():
        if line.lstrip().startswith(_FENCE):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        lines.append(f"**{match.group(1)}**" if match else line)
    return "\n".join(lines)


def table_body(table: TableDef) -> str:
    """`### テーブル: <名前>`の下の本文(説明と列の表)。"""
    lines = [table.description, ""] if table.description else []
    if table.columns:
        lines += md_table(
            ["カラム名", "データ型", "制約", "説明"],
            [[c.name, c.type, c.constraints, c.description] for c in table.columns],
        )
    else:
        lines.append(_NONE_ROW)
    return "\n".join(lines)


def dataflow_body(flow: DataFlowRow) -> str:
    """`#### DF-<n>: <トリガー>`の下の本文(流れの表とデータ項目)。"""
    lines = (
        md_table(
            ["元", "データ", "変換", "先"],
            [[f.source, f.data, f.transform, f.target] for f in flow.flows],
        )
        if flow.flows
        else [_NONE_ROW]
    )
    if flow.data_items:
        lines.append("")
        for item in flow.data_items:
            fields = f"({', '.join(item.fields)})" if item.fields else ""
            lines.append(f"- データ項目: {item.name}{fields}")
    return "\n".join(lines)


def data_model_body(model: InternalDesignModel) -> str:
    """3.2 の本文(見出しの行を除く。概要とテーブルごとの小節)。"""
    lines = [body_text(model.data_model_note)]
    for table in model.tables:
        lines += ["", f"### テーブル: {table.name}", "", table_body(table)]
    return "\n".join(lines)


def _apis(model: InternalDesignModel) -> list[str]:
    if not model.apis:
        return [_NONE_ROW]
    rows = [[a.method, a.path, a.summary] for a in model.apis]
    return md_table(["メソッド", "パス", "概要"], rows)


def _modules(model: InternalDesignModel) -> list[str]:
    if not model.modules:
        return [_NONE_ROW]
    return md_table(
        ["パス", "層", "責務", "主な依存先"],
        [[m.path, m.layer, m.responsibility, ", ".join(m.depends_on)] for m in model.modules],
    )


def _dataflows(model: InternalDesignModel) -> list[str]:
    if not model.dataflows:
        return [_NONE_ROW]
    lines: list[str] = []
    for flow in model.dataflows:
        lines += [f"#### {flow.id}: {flow.trigger}", "", dataflow_body(flow), ""]
    return lines[:-1]


def render_internal_design(model: InternalDesignModel) -> str:
    """内部設計書の md。モジュール一覧の小節は、行が無くても見出しを置く(無いことを表に出す)。"""
    lines = [TITLE, "", ARCHITECTURE_HEADING, "", body_text(model.architecture)]
    lines += ["", DATA_MODEL_HEADING, "", data_model_body(model)]
    lines += ["", MODULE_HEADING, "", body_text(model.module_policy)]
    lines += ["", "### APIエンドポイント一覧", "", *_apis(model)]
    lines += ["", "### モジュール一覧", "", *_modules(model)]
    lines += ["", "### 処理別データフロー", "", *_dataflows(model)]
    lines += ["", ERROR_POLICY_HEADING, "", body_text(model.error_policy)]
    return "\n".join(lines) + "\n"
