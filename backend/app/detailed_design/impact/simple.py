"""簡易ドキュメントモードの、変更の影響を求めるための要素と参照の導出(純粋関数)。

docs/internal_design.md 3.3節「7. 変更影響分析・変更管理」。

簡易モードは段階1〜7 を持たず、段階8の単位の手順書が4文書を直接参照する。そこで、4文書の model と
段階8の model から要素を作り、詳細設計モードと同じたどり方(trace.py)で単位の手順書に印を付ける。

- 文書の要素(置き場0): 要件・画面・API(詳細設計モードと共有)、内部設計書の処理別データフロー
  (`df:DF-1`)・テーブル・モジュール一覧の行、実装計画書のタスク(`task:U-001`)。
- 段階8の要素: 単位の手順書(`unit:U-001`)。手順書のキーで実装計画書のタスクに付くものだけ。
- 処理別データフローは、詳細設計モードの処理(段階1)に当たる「軸」。トリガーの API と
  テーブルを参照し、タスク・手順書から参照される。タスクも要件を直接持つので軸に含める(trace.py)。
- 単位が参照する設計(DF・モジュール)は、段階8の画面・生成と同じ規則で導く(`book_from_model`・
  `normalize_plan`・`simple_unit_refs`。モジュールは`module_layer`でモジュール一覧の行にそろえる)。
- 内部設計書 3.3 の API エンドポイント一覧の行は要素にしない(API の要素は外部設計書 2.6 の行)。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.detailed_design.api_list import trigger_key
from app.detailed_design.data_model import table_key
from app.detailed_design.impact.elements import (
    DOCUMENT_STAGE,
    Element,
    ElementBuilder,
    add_document_elements,
    parse_model,
    row_body,
    task_body,
)
from app.detailed_design.plan import PlanModel, normalize_plan
from app.detailed_design.procedure_doc import ProcedureDocModel, plan_units
from app.detailed_design.simple_procedure.internal_design import (
    SimpleDesignBook,
    book_from_model,
    module_layer,
)
from app.detailed_design.simple_procedure.refs import simple_unit_refs
from app.documents.external_design.model import ExternalDesignModel
from app.documents.implementation_plan.model import ImplementationPlanModel
from app.documents.internal_design.model import InternalDesignModel
from app.documents.requirements.model import RequirementsModel

# 単位の手順書の置き場(簡易モードは段階8だけを持つ)
UNIT_STAGE = 8


@dataclass(frozen=True)
class SimpleModels:
    """簡易モードの要素を導く入力(4文書の model と段階8の model)。無い文書は None。"""

    requirements: RequirementsModel | None = None
    external_design: ExternalDesignModel | None = None
    internal_design: InternalDesignModel | None = None
    implementation_plan: ImplementationPlanModel | None = None
    procedure_docs: ProcedureDocModel = field(default_factory=ProcedureDocModel)

    @classmethod
    def from_dicts(
        cls,
        stage8: Mapping[str, Any] | None,
        documents: Mapping[str, Mapping[str, Any] | None] | None = None,
    ) -> "SimpleModels":
        """保存された形(段階8の`model`・文書の`model`の dict)から作る。形の正しくないものは
        無いとみなす。"""
        documents = documents or {}
        return cls(
            requirements=parse_model(RequirementsModel, documents.get("requirements")),
            external_design=parse_model(ExternalDesignModel, documents.get("external_design")),
            internal_design=parse_model(InternalDesignModel, documents.get("internal_design")),
            implementation_plan=parse_model(
                ImplementationPlanModel, documents.get("implementation_plan")
            ),
            procedure_docs=parse_model(ProcedureDocModel, stage8) or ProcedureDocModel(),
        )


def build_simple_elements(models: SimpleModels) -> list[Element]:
    """簡易モードの要素と参照を作る(要件定義書・外部設計書 → 内部設計書 → 実装計画書 → 段階8)。"""
    b = ElementBuilder()
    add_document_elements(b, models.requirements, models.external_design)
    internal = models.internal_design or InternalDesignModel()
    book = book_from_model(internal)
    _internal_design(b, internal, book)
    plan = _plan(models.implementation_plan, book)
    _tasks_and_units(b, plan, book, models.procedure_docs)
    return b.build()


def _internal_design(b: ElementBuilder, model: InternalDesignModel, book: SimpleDesignBook) -> None:
    for table in model.tables:
        key = table_key(table.name)
        b.add(f"table:{key}", "table", DOCUMENT_STAGE, table.name.strip(), row_body(table, "name"))
    for module in model.modules:
        body = row_body(module, "path")
        b.add(f"module:{module.path}", "module", DOCUMENT_STAGE, module.path, body)
    for module in model.modules:
        for dep in module.depends_on:
            b.ref(f"module:{module.path}", f"module:{dep.strip()}", "depends")
    for flow in model.dataflows:
        label = f"{flow.id} {flow.trigger}"
        key = b.add(f"df:{flow.id}", "dataflow", DOCUMENT_STAGE, label, row_body(flow, "id"))
        api = trigger_key(flow.trigger)
        if api is not None:
            b.ref(key, f"api:{api}", "trigger")
        parsed = book.dataflows.get(flow.id)
        for name in parsed.tables if parsed is not None else ():
            b.ref(key, f"table:{table_key(name)}", "table")


def _plan(model: ImplementationPlanModel | None, book: SimpleDesignBook) -> PlanModel:
    """段階8と同じ計画(モジュールをモジュール一覧のパスにそろえる。procedure_basis と同じ)。"""
    if model is None:
        return PlanModel()
    return normalize_plan(model.to_plan(), list(book.modules))


def _module_key(path: str, book: SimpleDesignBook) -> str | None:
    """ファイルのパスが属するモジュール一覧の行の要素のキー。層が決まらなければ None。"""
    row = module_layer(path, book)
    return f"module:{row.path}" if row is not None else None


def _tasks_and_units(
    b: ElementBuilder, plan: PlanModel, book: SimpleDesignBook, docs: ProcedureDocModel
) -> None:
    units = plan_units(plan)
    for unit in units:
        label = f"{unit.unit_id} {unit.task.title}"
        key = b.add(f"task:{unit.key}", "task", DOCUMENT_STAGE, label, task_body(unit, units))
        for df in unit.task.function_ids:
            b.ref(key, f"df:{df.strip()}", "function")
        for req in unit.task.req_ids:
            b.ref(key, f"req:{req}", "requirement")
        for path in unit.task.modules:
            module = _module_key(path, book)
            if module is not None:
                b.ref(key, module, "depends")
    by_key = {unit.key: unit for unit in units}
    for doc in docs.units:
        unit = by_key.get(doc.unit_key or doc.unit_id)
        if unit is None:
            continue
        label = f"{unit.unit_id} の手順書"
        body = row_body(doc, "unit_id", "unit_key")
        key = b.add(f"unit:{unit.key}", "unit", UNIT_STAGE, label, body)
        b.ref(key, f"task:{unit.key}", "unit")
        paths: list[str] = []
        for ref in simple_unit_refs(unit.task, book):
            if ref.kind == "dataflow":
                b.ref(key, f"df:{ref.key}", "implements")
            elif ref.kind == "module":
                paths.append(ref.key)
        paths += [f.path.strip() for f in doc.files if f.kind == "module"]
        for path in dict.fromkeys(paths):
            module = _module_key(path, book)
            if module is not None:
                b.ref(key, module, "depends")
