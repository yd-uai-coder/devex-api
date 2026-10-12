"""AI の下書きから内部設計書の model を組み立てる(純粋関数)。

- 処理別データフローの ID: AI には書かせない。前の版に同じ処理(同じ API の照合のキー・同じバッチ
  名)があればその ID を引き継ぎ、無ければ次の番号を振る。同じ処理の2つ目は捨てる。
- API: メソッドを大文字にそろえ、形の違う行(知らないメソッド・`/`で始まらないパス)と、同じ
  API の2行目を捨てる(`app.detailed_design.api_list`が読める形だけを残す)。
- テーブル・モジュール: 名前・パスの無い行と、同じ名前・パスの2行目を捨てる。
- 変更なし(`unchanged`): 前の版の同じ名前のテーブル・同じパスのモジュール・同じ API・同じ処理の
  処理別データフローがあれば、その行をそのまま使う(`app.documents.revision`)。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.detailed_design.api_list import HTTP_METHODS, endpoint_key
from app.detailed_design.structure import ModuleRow
from app.documents.internal_design.model import (
    ColumnRow,
    DataFlowRow,
    DataItem,
    FlowRow,
    InternalApiRow,
    InternalDesignModel,
    TableDef,
    dataflow_id,
    dataflow_seq,
)

_EMPTY_CELLS = frozenset({"", "—", "-", "なし"})


@dataclass(frozen=True)
class ColumnDraft:
    name: str
    type: str = ""
    constraints: str = ""
    description: str = ""


@dataclass(frozen=True)
class TableDraft:
    name: str
    description: str = ""
    columns: tuple[ColumnDraft, ...] = ()
    unchanged: bool = False


@dataclass(frozen=True)
class InternalApiDraft:
    method: str
    path: str
    summary: str = ""
    unchanged: bool = False


@dataclass(frozen=True)
class ModuleDraft:
    path: str
    layer: str = ""
    responsibility: str = ""
    depends_on: tuple[str, ...] = ()
    unchanged: bool = False


@dataclass(frozen=True)
class FlowDraft:
    source: str
    data: str = ""
    transform: str = ""
    target: str = ""


@dataclass(frozen=True)
class DataItemDraft:
    name: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataFlowDraft:
    """AI が下書きした処理別データフロー1つ(ID は持たない)。"""

    trigger: str
    flows: tuple[FlowDraft, ...] = ()
    data_items: tuple[DataItemDraft, ...] = ()
    unchanged: bool = False


@dataclass(frozen=True)
class InternalDesignDraft:
    """AI の下書き1回分(構造化出力から`drafting.to_draft`で作る)。"""

    architecture: str = ""
    data_model_note: str = ""
    tables: tuple[TableDraft, ...] = ()
    module_policy: str = ""
    apis: tuple[InternalApiDraft, ...] = ()
    modules: tuple[ModuleDraft, ...] = ()
    dataflows: tuple[DataFlowDraft, ...] = ()
    error_policy: str = ""


def _plain(value: str) -> str:
    return value.strip().strip("`").strip()


def _cell(value: str) -> str:
    value = value.strip()
    return "" if value in _EMPTY_CELLS else value


def _tables(drafts: Iterable[TableDraft], previous: InternalDesignModel | None) -> list[TableDef]:
    same = {t.name: t for t in previous.tables} if previous else {}
    tables: list[TableDef] = []
    seen: set[str] = set()
    for d in drafts:
        name = _plain(d.name)
        if not name or name in seen:
            continue
        seen.add(name)
        if d.unchanged and name in same:
            tables.append(same[name])
            continue
        columns = [
            ColumnRow(
                name=_plain(c.name),
                type=c.type.strip(),
                constraints=_cell(c.constraints),
                description=c.description.strip(),
            )
            for c in d.columns
            if _plain(c.name)
        ]
        tables.append(TableDef(name=name, description=d.description.strip(), columns=columns))
    return tables


def _apis(
    drafts: Iterable[InternalApiDraft], previous: InternalDesignModel | None
) -> list[InternalApiRow]:
    same = {a.key: a for a in previous.apis} if previous else {}
    rows: list[InternalApiRow] = []
    seen: set[str] = set()
    for d in drafts:
        method = d.method.strip().upper()
        path = _plain(d.path)
        if method not in HTTP_METHODS or not path.startswith("/"):
            continue
        key = endpoint_key(method, path)
        if key in seen:
            continue
        seen.add(key)
        if d.unchanged and key in same:
            rows.append(same[key])
            continue
        rows.append(InternalApiRow(method=method, path=path, summary=d.summary.strip()))
    return rows


def _modules(
    drafts: Iterable[ModuleDraft], previous: InternalDesignModel | None
) -> list[ModuleRow]:
    same = {m.path: m for m in previous.modules} if previous else {}
    rows: list[ModuleRow] = []
    seen: set[str] = set()
    for d in drafts:
        path = _plain(d.path)
        if not path or path in seen:
            continue
        seen.add(path)
        if d.unchanged and path in same:
            rows.append(same[path])
            continue
        depends = [_plain(x) for x in d.depends_on if _plain(x) not in _EMPTY_CELLS]
        rows.append(
            ModuleRow(
                path=path,
                layer=d.layer.strip(),
                responsibility=d.responsibility.strip(),
                depends_on=list(dict.fromkeys(depends)),
            )
        )
    return rows


def _dataflows(
    drafts: Iterable[DataFlowDraft], previous: InternalDesignModel | None
) -> tuple[list[DataFlowRow], int]:
    """処理別データフローの行と、次の番号。"""
    prev_rows = previous.dataflows if previous else []
    same = {row.match_key: row for row in prev_rows}
    seqs = [s for s in (dataflow_seq(row.id) for row in prev_rows) if s is not None]
    seq = max([previous.next_dataflow_seq if previous else 1, *(s + 1 for s in seqs)])
    rows: list[DataFlowRow] = []
    seen: set[str] = set()
    for d in drafts:
        trigger = " ".join(_plain(d.trigger).split())
        if not trigger:
            continue
        row = DataFlowRow(
            id="",
            trigger=trigger,
            flows=[
                FlowRow(
                    source=f.source.strip(),
                    data=f.data.strip(),
                    transform=f.transform.strip(),
                    target=f.target.strip(),
                )
                for f in d.flows
                if f.source.strip() or f.target.strip()
            ],
            data_items=[
                DataItem(name=i.name.strip(), fields=[f.strip() for f in i.fields if f.strip()])
                for i in d.data_items
                if i.name.strip()
            ],
        )
        if row.match_key in seen:
            continue
        seen.add(row.match_key)
        prev = same.get(row.match_key)
        if prev is not None and d.unchanged:
            rows.append(prev)
            continue
        if prev is None:
            carried, seq = dataflow_id(seq), seq + 1
        else:
            carried = prev.id
        rows.append(row.model_copy(update={"id": carried}))
    return rows, seq


def build_model(
    draft: InternalDesignDraft,
    previous: InternalDesignModel | None,
    external_apis: Sequence[str] = (),
) -> InternalDesignModel:
    """下書きから model を作る。生成した時点の外部設計書の API(`POST /api/v1/x`)を写して持つ
    (検証が読む)。"""
    dataflows, next_seq = _dataflows(draft.dataflows, previous)
    return InternalDesignModel(
        architecture=draft.architecture.strip(),
        data_model_note=draft.data_model_note.strip(),
        tables=_tables(draft.tables, previous),
        module_policy=draft.module_policy.strip(),
        apis=_apis(draft.apis, previous),
        modules=_modules(draft.modules, previous),
        dataflows=dataflows,
        error_policy=draft.error_policy.strip(),
        external_apis=list(dict.fromkeys(a.strip() for a in external_apis if a.strip())),
        next_dataflow_seq=next_seq,
    )
