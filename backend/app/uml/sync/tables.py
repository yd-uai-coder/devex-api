"""承認済みの図の意味モデルから、内部設計書に差し込む要素表(Markdown)を決定的に作る純粋関数群。

記法ごとの列(appendix/stage3-requirements-organization.md の決定):
- component: 名称 / 種別 / 説明 / 依存先
- ER: テーブル / カラム / 型 / PK / FK / NULL と、関連(元 / 先 / 多重度)
- DFD: 元 / データ / 変換 / 先 と、データ項目(名前とフィールドの一覧)

DFDの「変換」は、そのフローを出力した処理(元が処理ノード)の説明である。処理はデータを
受け取って加工し、次へ渡すので、「どう加工された結果のデータか」を元の処理に持たせる
(元が外部実体・データストアのフローは「—」)。データ辞書はDBにあるため、呼び出し側が
`DataItemSummary`に詰めて渡す(この関数はI/Oを持たない)。
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from app.uml.domain import (
    ComponentSemanticModel,
    DfdProcess,
    DfdSemanticModel,
    ErSemanticModel,
)

_EMPTY = "—"

_ER_MULTIPLICITY = {"one_to_one": "1対1", "one_to_many": "1対多", "many_to_many": "多対多"}


@dataclass(frozen=True)
class DataItemSummary:
    """要素表に載せるデータ項目1件分(名前とフィールド名の一覧。M2bのデータ辞書の骨格)。"""

    name: str
    field_names: tuple[str, ...]


def render_element_table(
    model: ComponentSemanticModel | ErSemanticModel | DfdSemanticModel,
    data_items: Mapping[uuid.UUID, DataItemSummary],
) -> str:
    """意味モデルの記法に応じた要素表を返す(`data_items`はDFDだけが使う)。"""
    if isinstance(model, ComponentSemanticModel):
        return _component_table(model)
    if isinstance(model, ErSemanticModel):
        return _er_tables(model)
    return _dfd_tables(model, data_items)


def render_block_body(title: str, table: str) -> str:
    """アンカーの範囲に入れる本文(図の題名と注意書き+要素表)。この範囲は反映のたびに
    上書きされるため、手で編集しないよう注意書きを添える(D1案A: in-placeで置き換える)。"""
    notice = (
        f"> 図: {title} ── 承認済みの UML 図から自動生成した要素表です。"
        "図を再反映すると上書きされます。"
    )
    return f"{notice}\n\n{table}"


def _component_table(model: ComponentSemanticModel) -> str:
    names = {el.id: el.name for el in model.elements}
    rows = []
    for element in model.elements:
        targets = [names[r.target_id] for r in model.relations if r.source_id == element.id]
        rows.append(
            [
                element.name,
                "モジュール",
                element.description or _EMPTY,
                ", ".join(targets) or _EMPTY,
            ]
        )
    return _markdown_table(["名称", "種別", "説明", "依存先"], rows)


def _er_tables(model: ErSemanticModel) -> str:
    column_rows = [
        [
            table.name,
            column.name,
            column.type,
            "○" if column.is_primary_key else "",
            "○" if column.is_foreign_key else "",
            "可" if column.nullable else "不可",
        ]
        for table in model.elements
        for column in table.columns
    ]
    parts = [_markdown_table(["テーブル", "カラム", "型", "PK", "FK", "NULL"], column_rows)]
    if model.relations:
        names = {el.id: el.name for el in model.elements}
        relation_rows = [
            [names[r.source_id], names[r.target_id], _ER_MULTIPLICITY[r.relation_type]]
            for r in model.relations
        ]
        parts.append(_markdown_table(["元", "先", "多重度"], relation_rows))
    return "\n\n".join(parts)


def _dfd_tables(model: DfdSemanticModel, data_items: Mapping[uuid.UUID, DataItemSummary]) -> str:
    elements = {el.id: el for el in model.elements}
    flow_rows = []
    used: list[uuid.UUID] = []
    for flow in model.relations:
        source = elements[flow.source_id]
        item = data_items.get(flow.data_item_id)
        transform = (source.description or _EMPTY) if isinstance(source, DfdProcess) else _EMPTY
        flow_rows.append(
            [
                source.name,
                item.name if item else _EMPTY,
                transform,
                elements[flow.target_id].name,
            ]
        )
        if item is not None and flow.data_item_id not in used:
            used.append(flow.data_item_id)
    lines = [_markdown_table(["元", "データ", "変換", "先"], flow_rows)]
    if used:
        lines.append(
            "\n".join(
                f"- データ項目: {data_items[i].name}({', '.join(data_items[i].field_names)})"
                for i in used
            )
        )
    return "\n\n".join(lines)


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    """Markdownの表を作る。セルの`|`と改行は表を壊すため、エスケープ・空白に置き換える。"""
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ")
