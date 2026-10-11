"""変更の影響を求めるための、設計の要素と参照の導出(純粋関数)。

docs/internal_design.md 3.3節「7. 変更影響分析・変更管理」。

要件定義書・外部設計書の model と段階1〜8 の model から、要素(キー・置き場・ラベル)と、
「A が B を参照する(A は B に依存する)」辺を種類つきで作る。

- 辺は model の ID の欄だけから作り、本文の文字列からは作らない(言い回しで辺が増減しないため)。
  例外は段階5の手順の行の DB 欄で、テーブル名を読む(`parse_db_tables`)。手順と、同じ行の
  呼び出し先のモジュールから、そのテーブルへの辺を作る。段階3の CRUD・ER に在るテーブル名だけを
  辺にし、知らない名前は捨てる。
- キーは既存の ID に種類の接頭辞を付けたもの(`req:FR-001`・`fn:F-01`・`module:<パス>` など)。
  作業単位と手順書は、段階7のタスクの安定キー(`U-001`)で指す。
- グラフは保存せず、読むたびに組む(要素の数は数百で、組むのは一瞬のため)。
- 外部のグラフライブラリは使わない(辞書と集合で足りる)。
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.detailed_design.api_list import endpoint_key, trigger_key
from app.detailed_design.data_flow import DataFlowModel
from app.detailed_design.data_model import CrudModel, table_key
from app.detailed_design.function_list import FunctionListModel
from app.detailed_design.logic import LogicModel, logic_key
from app.detailed_design.plan import PlanModel
from app.detailed_design.procedure import ProcedureModel, ProcedureStep, calls_function
from app.detailed_design.procedure_doc import (
    PlanUnit,
    ProcedureDocModel,
    design_index,
    plan_units,
    unit_refs,
)
from app.detailed_design.structure import ModuleListModel
from app.documents.external_design.model import ExternalDesignModel
from app.documents.requirements.model import RequirementsModel

# 要素の種類
ElementKind = Literal[
    "requirement",
    "screen",
    "api",
    "function",
    "summary",
    "table",
    "crud",
    "module",
    "procedure",
    "logic",
    "task",
    "crosscutting",
    "unit",
]

# 参照の種類(A が B を参照する)。理由の文は trace.py の`EDGE_REASON`
EdgeKind = Literal[
    "requirement",  # 要件ID を持つ
    "screen",  # 関連画面
    "trigger",  # API のトリガー
    "function",  # 処理に属する
    "table",  # テーブルを読み書きする
    "depends",  # モジュールに依存する(モジュールの依存先・作業単位・横断事項・手順書のファイル)
    "calls",  # 手順の呼び出し先
    "placed",  # 処理ロジックの置き場
    "called_by",  # 処理ロジックを呼ぶ手順
    "implements",  # 手順書が参照する設計(手順・処理ロジック)
    "unit",  # 作業単位の手順書
    "covers",  # 横断事項の対象のモジュールを含む
]

# 置き場。0 = 文書(要件定義書・外部設計書)、1〜8 = 詳細設計の段階
DOCUMENT_STAGE = 0

# 手順の行の DB 欄の「テーブル名 + 操作(C/R/U/D)」(例: `reservations R`・`users CU`)
_DB_TABLE = re.compile(r"([A-Za-z_][\w.]*)\s+[CRUD]+\b")


@dataclass(frozen=True)
class Ref:
    """参照1本(参照先のキーと種類)。"""

    to: str
    edge: EdgeKind


@dataclass(frozen=True)
class Element:
    """設計の要素1つ。`stage`は置き場(0 は文書)、`refs`はこの要素が参照する要素。"""

    key: str
    kind: ElementKind
    stage: int
    label: str
    refs: tuple[Ref, ...] = ()


@dataclass(frozen=True)
class DesignModels:
    """要素を導く入力(文書の model と段階1〜8 の model)。無い文書・段階は空の model。
    `er_tables`は段階3の ER のテーブル名(ER は段階の model の外の図にあるため別に渡す)。"""

    functions: FunctionListModel = field(default_factory=FunctionListModel)
    data_flow: DataFlowModel = field(default_factory=DataFlowModel)
    crud: CrudModel = field(default_factory=CrudModel)
    modules: ModuleListModel = field(default_factory=ModuleListModel)
    procedures: ProcedureModel = field(default_factory=ProcedureModel)
    logics: LogicModel = field(default_factory=LogicModel)
    plan: PlanModel = field(default_factory=PlanModel)
    procedure_docs: ProcedureDocModel = field(default_factory=ProcedureDocModel)
    requirements: RequirementsModel | None = None
    external_design: ExternalDesignModel | None = None
    er_tables: tuple[str, ...] = ()

    @classmethod
    def from_dicts(
        cls,
        stages: Mapping[int, Mapping[str, Any] | None],
        documents: Mapping[str, Mapping[str, Any] | None] | None = None,
        er_tables: Iterable[str] = (),
    ) -> "DesignModels":
        """保存された形(段階の`model`・文書の`model`の dict)から作る。形の正しくない段階・文書は
        空とみなす(検証が別に指摘するため、ここでは落とさない)。"""
        documents = documents or {}
        return cls(
            functions=_parse(FunctionListModel, stages.get(1)) or FunctionListModel(),
            data_flow=_parse(DataFlowModel, stages.get(2)) or DataFlowModel(),
            crud=_parse(CrudModel, stages.get(3)) or CrudModel(),
            modules=_parse(ModuleListModel, stages.get(4)) or ModuleListModel(),
            procedures=_parse(ProcedureModel, stages.get(5)) or ProcedureModel(),
            logics=_parse(LogicModel, stages.get(6)) or LogicModel(),
            plan=_parse(PlanModel, stages.get(7)) or PlanModel(),
            procedure_docs=_parse(ProcedureDocModel, stages.get(8)) or ProcedureDocModel(),
            requirements=_parse(RequirementsModel, documents.get("requirements")),
            external_design=_parse(ExternalDesignModel, documents.get("external_design")),
            er_tables=tuple(er_tables),
        )


def _parse[M: BaseModel](model_type: type[M], value: Mapping[str, Any] | None) -> M | None:
    if not value:
        return None
    try:
        return model_type.model_validate(value)
    except ValidationError:
        return None


def parse_db_tables(db: str, known_tables: Iterable[str]) -> list[str]:
    """手順の行の DB 欄から、読み書きするテーブルのキー(`table_key`)を取り出す(現れた順・重複なし)。
    `known_tables`(テーブルのキー)に無い名前は捨てる(`—`や本文の言葉を拾わないため)。"""
    known = set(known_tables)
    found = (table_key(name) for name in _DB_TABLE.findall(db))
    return list(dict.fromkeys(name for name in found if name in known))


class _Builder:
    """要素を足していく入れ物。辺は、参照先が要素として在るものだけを残す(`build`の最後で)。"""

    def __init__(self) -> None:
        self.elements: dict[str, Element] = {}
        self.refs: dict[str, list[Ref]] = {}

    def add(self, key: str, kind: ElementKind, stage: int, label: str) -> str:
        if key not in self.elements:
            self.elements[key] = Element(key, kind, stage, label)
            self.refs[key] = []
        return key

    def ref(self, src: str, to: str, edge: EdgeKind) -> None:
        if src != to and Ref(to, edge) not in self.refs[src]:
            self.refs[src].append(Ref(to, edge))

    def build(self) -> list[Element]:
        return [
            Element(
                e.key,
                e.kind,
                e.stage,
                e.label,
                tuple(r for r in self.refs[e.key] if r.to in self.elements),
            )
            for e in self.elements.values()
        ]


def build_elements(models: DesignModels) -> list[Element]:
    """設計の要素と参照を作る(文書 → 段階1〜8 の順。同じ置き場の中は model の並び順)。"""
    b = _Builder()
    _documents(b, models)
    _stages_1_to_4(b, models)
    _stages_5_to_8(b, models)
    return b.build()


def _documents(b: _Builder, models: DesignModels) -> None:
    if models.requirements is not None:
        for row in models.requirements.rows:
            b.add(f"req:{row.id}", "requirement", DOCUMENT_STAGE, f"{row.id} {row.text}")
    if models.external_design is not None:
        for screen in models.external_design.screens:
            label = f"{screen.id} {screen.name}"
            key = b.add(f"screen:{screen.id}", "screen", DOCUMENT_STAGE, label)
            for req in screen.req_ids:
                b.ref(key, f"req:{req}", "requirement")
        for api in models.external_design.apis:
            label = f"{api.method} {api.path}"
            key = b.add(f"api:{endpoint_key(api.method, api.path)}", "api", DOCUMENT_STAGE, label)
            for screen_id in api.screens:
                b.ref(key, f"screen:{screen_id}", "screen")
            for req in api.req_ids:
                b.ref(key, f"req:{req}", "requirement")


def _stages_1_to_4(b: _Builder, models: DesignModels) -> None:
    for row in models.functions.functions:
        key = b.add(f"fn:{row.id}", "function", 1, f"{row.id} {row.name}")
        for req in row.req_ids:
            b.ref(key, f"req:{req}", "requirement")
        for screen_id in row.screens:
            b.ref(key, f"screen:{screen_id}", "screen")
        api = trigger_key(row.trigger)
        if api is not None:
            b.ref(key, f"api:{api}", "trigger")
    for summary in models.data_flow.summaries:
        fid = summary.function_id
        key = b.add(f"summary:{fid}", "summary", 2, f"{fid} の処理の要約")
        b.ref(key, f"fn:{fid}", "function")
    for name in [*models.er_tables, *(cell.table for cell in models.crud.cells)]:
        b.add(f"table:{table_key(name)}", "table", 3, name.strip())
    for cell in models.crud.cells:
        fid, table = cell.function_id, table_key(cell.table)
        key = b.add(f"crud:{fid}×{table}", "crud", 3, f"{fid} × {cell.table.strip()}")
        b.ref(key, f"fn:{fid}", "function")
        b.ref(key, f"table:{table}", "table")
    for module in models.modules.modules:
        b.add(f"module:{module.path}", "module", 4, module.path)
    for module in models.modules.modules:
        key = f"module:{module.path}"
        for fid in module.functions:
            b.ref(key, f"fn:{fid}", "function")
        for dep in module.depends_on:
            b.ref(key, f"module:{dep}", "depends")


def _stages_5_to_8(b: _Builder, models: DesignModels) -> None:
    tables = [k.removeprefix("table:") for k in b.elements if k.startswith("table:")]
    for procedure in models.procedures.procedures:
        fid = procedure.function_id
        key = b.add(f"proc:{fid}", "procedure", 5, f"{fid} の手順")
        b.ref(key, f"fn:{fid}", "function")
        for step in procedure.steps:
            module = f"module:{step.callee.strip()}"
            if calls_function(step):
                b.ref(key, module, "calls")
            for table in parse_db_tables(step.db, tables):
                b.ref(key, f"table:{table}", "table")
                # 同じ行の呼び出し先のモジュールが、そのテーブルを読み書きする(段階4には持たない)
                if module in b.elements:
                    b.ref(module, f"table:{table}", "table")
    for logic in models.logics.logics:
        name = logic_key(logic.module, logic.function)
        key = b.add(f"logic:{name}", "logic", 6, f"{logic.module} の {logic.function}")
        b.ref(key, f"module:{logic.module.strip()}", "placed")
        for procedure in models.procedures.procedures:
            if any(_calls(step, logic.module, logic.function) for step in procedure.steps):
                b.ref(key, f"proc:{procedure.function_id}", "called_by")
    units = plan_units(models.plan)
    for unit in units:
        key = b.add(f"task:{unit.key}", "task", 7, f"{unit.unit_id} {unit.task.title}")
        for fid in unit.task.function_ids:
            b.ref(key, f"fn:{fid}", "function")
        for path in unit.task.modules:
            b.ref(key, f"module:{path}", "depends")
    for row in models.plan.crosscutting:
        key = b.add(f"cc:{row.topic}", "crosscutting", 7, row.topic)
        for req in row.req_ids:
            b.ref(key, f"req:{req}", "requirement")
        for path in row.modules:
            b.ref(key, f"module:{path}", "depends")
    _units(b, models, units)


def _units(b: _Builder, models: DesignModels, units: list[PlanUnit]) -> None:
    """段階8の手順書(キーで段階7の単位に付くものだけ)。参照する設計(手順・処理ロジック・
    モジュール)は、段階8の画面・生成と同じ`unit_refs`で導き、手順書のモジュールのファイルも足す。"""
    index = design_index(
        {
            3: models.crud.model_dump(),
            4: models.modules.model_dump(),
            5: models.procedures.model_dump(),
            6: models.logics.model_dump(),
        }
    )
    by_key = {unit.key: unit for unit in units}
    for doc in models.procedure_docs.units:
        unit = by_key.get(doc.unit_key or doc.unit_id)
        if unit is None:
            continue
        key = b.add(f"unit:{unit.key}", "unit", 8, f"{unit.unit_id} の手順書")
        b.ref(key, f"task:{unit.key}", "unit")
        refs = unit_refs(unit.task, index)
        paths = [r.key for r in refs if r.kind == "module"]
        paths += [f.path.strip() for f in doc.files if f.kind == "module"]
        for ref in refs:
            if ref.kind == "procedure":
                b.ref(key, f"proc:{ref.key}", "implements")
            elif ref.kind == "logic":
                b.ref(key, f"logic:{ref.key}", "implements")
        for path in dict.fromkeys(paths):
            b.ref(key, f"module:{path}", "depends")
        for row in models.plan.crosscutting:
            if set(paths) & {p.strip() for p in row.modules}:
                b.ref(key, f"cc:{row.topic}", "covers")


def _calls(step: ProcedureStep, module: str, function: str) -> bool:
    """手順の行が、そのモジュールの関数を呼ぶか。"""
    return (
        calls_function(step)
        and step.callee.strip() == module.strip()
        and step.call.strip() == function.strip()
    )
