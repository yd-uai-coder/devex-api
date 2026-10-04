"""詳細設計書の表を、承認済みの意味モデルから導く(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」の「詳細設計書の組み立て」。

md と HTML は同じ表を出すので、表の中身はここで1回だけ導き、書き方(md の表・HTML のタブとバッジ)
だけを markdown.py・html.py に分ける。導いた値は保存しない。

- 手順番号・手順ID は`number_steps`・`step_id`(段階5の画面と同じ規則)。
- L-ID は段階6の並び順から`logic_id`。05↔06 の紐づけは、手順の(呼び出し先, 関数)と段階6の
  (モジュール, 関数)の一致から`logic_key`で導く(Phase 20 の決定。デモの`logic`欄は使わない)。
- CRUD 図の記号は、DFD の線(`dfd_accesses`)から決まる部分と人が確定した部分を分けて見せる
  (Phase 18 からの持ち越し。出力見本 appendix/detailed-design-devex の3分類)。
- 実装計画の「処理の割り当て」は、処理ごとに、その処理を書いたマイルストーンの M-ID を導く
  (Phase 23。計画の漏れが表で見える)。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.detailed_design.data_model import CrudModel, dfd_accesses, table_key
from app.detailed_design.function_list import FunctionListModel, FunctionRow
from app.detailed_design.logic import LogicModel, LogicRow, calling_steps, logic_id, logic_key
from app.detailed_design.plan import PlanModel, milestone_id
from app.detailed_design.procedure import (
    Procedure,
    ProcedureModel,
    ProcedureStep,
    is_external_actor,
    number_steps,
    step_id,
)
from app.detailed_design.structure import ModuleListModel
from app.uml.domain.er import ErSemanticModel


def anchor(identifier: str) -> str:
    """ID を HTML のアンカーにする(`F-01#4` → `f-01-4`、`L-02` → `l-02`)。`#`は URL の
    フラグメントの区切りと重なるため`-`に置き換える。"""
    return identifier.lower().replace("#", "-")


def functions_by_id(function_list: FunctionListModel | None) -> dict[str, FunctionRow]:
    """処理ID → 機能一覧の行(段階1が未承認なら空)。"""
    if function_list is None:
        return {}
    return {row.id: row for row in function_list.functions}


# ---------------------------------------------------------------------------
# 05 主要処理の手順 ↔ 06 処理ロジックの詳細
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepView:
    """手順の表の1行。`logic_id`は、この手順が呼ぶ関数の詳細(06)の L-ID(無ければ None)。"""

    number: str
    step_id: str
    step: ProcedureStep
    logic_id: str | None


@dataclass(frozen=True)
class LogicView:
    """06 の項目1つ。`step_ids`はこの関数を呼ぶ手順(呼ばれる手順)。"""

    logic_id: str
    row: LogicRow
    step_ids: tuple[str, ...]


def logic_ids(logics: LogicModel | None) -> dict[str, str]:
    """`logic_key` → L-ID(段階6が未承認・省略なら空。05 に詳細のバッジを出さない)。"""
    if logics is None:
        return {}
    return {logic_key(row.module, row.function): logic_id(i) for i, row in enumerate(logics.logics)}


def procedure_steps(procedure: Procedure, ids: Mapping[str, str]) -> list[StepView]:
    """1つの処理の手順の表の行(番号・手順ID・06 の L-ID つき)。分岐の行は関数を呼ばない。"""
    numbers = number_steps(procedure.steps)
    rows: list[StepView] = []
    for step, number in zip(procedure.steps, numbers, strict=True):
        linked = None
        if not step.is_branch and step.call.strip():
            linked = ids.get(logic_key(step.callee, step.call))
        rows.append(StepView(number, step_id(procedure.function_id, number), step, linked))
    return rows


def linked_logic_ids(steps: Iterable[StepView]) -> list[str]:
    """手順の表が呼ぶ関数の L-ID(重複を除き、手順の順)。05 の索引の「詳細(06)」の列。"""
    return list(dict.fromkeys(s.logic_id for s in steps if s.logic_id is not None))


def main_step_count(procedure: Procedure) -> int:
    """分岐を除いた手順の数(05 の索引の「手順数」)。"""
    return sum(1 for step in procedure.steps if not step.is_branch)


def logic_views(logics: LogicModel | None, procedures: ProcedureModel | None) -> list[LogicView]:
    """06 の項目(L-ID・呼ばれる手順つき)。呼ばれる手順は段階5から導く(06 の逆引き表も同じ値)。"""
    if logics is None:
        return []
    source = procedures or ProcedureModel()
    return [
        LogicView(
            logic_id=logic_id(i),
            row=row,
            step_ids=tuple(calling_steps(source, row.module, row.function)),
        )
        for i, row in enumerate(logics.logics)
    ]


@dataclass(frozen=True)
class Involvement:
    """処理 × モジュールの関与表。`cells[処理ID][パス]`はその処理がそのモジュールを呼ぶ手順番号。"""

    modules: list[str]
    cells: dict[str, dict[str, list[str]]]


def involvement(procedures: ProcedureModel, modules: ModuleListModel | None) -> Involvement:
    """関与表を導く。列はモジュール一覧の並び(呼ばれたものだけ)。外部の役者と分岐の行は除く。"""
    cells: dict[str, dict[str, list[str]]] = {}
    called: list[str] = []
    for procedure in procedures.procedures:
        row = cells.setdefault(procedure.function_id, {})
        numbers = number_steps(procedure.steps)
        for step, number in zip(procedure.steps, numbers, strict=True):
            callee = step.callee.strip()
            if step.is_branch or not callee or is_external_actor(callee):
                continue
            row.setdefault(callee, []).append(number)
            if callee not in called:
                called.append(callee)
    order = [m.path for m in modules.modules] if modules is not None else []
    columns = [path for path in order if path in called]
    columns += [path for path in called if path not in columns]
    return Involvement(modules=columns, cells=cells)


# ---------------------------------------------------------------------------
# 02 データ辞書 / 03 CRUD 図
# ---------------------------------------------------------------------------


def data_item_usage(dfd_models: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """データ項目の id → それを受け渡す処理の処理ID(DFD の線の端の処理の箱。現れた順)。
    DFD の処理の箱の id は段階1の処理ID(Phase 17 の決定)。"""
    usage: dict[str, list[str]] = {}
    for model in dfd_models:
        processes = {
            str(e.get("id"))
            for e in model.get("elements", [])
            if e.get("element_type") == "process"
        }
        for relation in model.get("relations", []):
            item = str(relation.get("data_item_id", ""))
            for end in (str(relation.get("source_id")), str(relation.get("target_id"))):
                if end in processes and end not in usage.setdefault(item, []):
                    usage[item].append(end)
    return usage


CrudMarkKind = Literal["dfd", "dfd_write", "human"]


@dataclass(frozen=True)
class CrudMark:
    """CRUD 図のセルの操作1つと、その決まり方。

    - `dfd`: DFD の線(ストア → 処理)から決まる R。
    - `dfd_write`: 書き込みがあることは DFD の線(処理 → ストア)から決まり、C/U/D の区別は人が確定。
    - `human`: DFD に描いていない処理・線の分。AI の下書きを人が確定した。
    """

    op: str
    kind: CrudMarkKind


@dataclass(frozen=True)
class CrudMatrix:
    """CRUD 図(処理 × テーブル)。`rows`は(処理ID, テーブル名 → 記号)の並び。"""

    tables: list[str]
    rows: list[tuple[str, dict[str, list[CrudMark]]]]


def crud_matrix(
    crud: CrudModel,
    er: ErSemanticModel | None,
    dfd_models: Sequence[Mapping[str, Any]],
    function_order: Sequence[str] = (),
) -> CrudMatrix:
    """CRUD 図の表を導く。列は ER のテーブルの並び、行は機能一覧の並び(操作のある処理だけ)。"""
    accesses = {(a.function_id, a.table, a.kind) for a in dfd_accesses(dfd_models)}
    by_function: dict[str, dict[str, list[CrudMark]]] = {}
    tables_seen: list[str] = []
    for cell in crud.cells:
        if not cell.ops:
            continue
        marks = [_mark(op, cell.function_id, cell.table, accesses) for op in cell.ops]
        by_function.setdefault(cell.function_id, {})[cell.table] = marks
        if cell.table not in tables_seen:
            tables_seen.append(cell.table)
    er_tables = [element.name for element in er.elements] if er is not None else []
    tables = er_tables + [t for t in tables_seen if t not in er_tables]
    order = [fid for fid in function_order if fid in by_function]
    order += [fid for fid in by_function if fid not in order]
    return CrudMatrix(tables=tables, rows=[(fid, by_function[fid]) for fid in order])


def _mark(op: str, function_id: str, table: str, accesses: set[tuple[str, str, str]]) -> CrudMark:
    key = table_key(table)
    if op == "R" and (function_id, key, "read") in accesses:
        return CrudMark(op, "dfd")
    if op in "CUD" and (function_id, key, "write") in accesses:
        return CrudMark(op, "dfd_write")
    return CrudMark(op, "human")


# ---------------------------------------------------------------------------
# 実装計画: 処理の割り当て(Phase 23)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FunctionPlan:
    """処理1つの割り当て。`milestones`は、その処理をマイルストーンかタスクに書いた M-ID
    (計画の並び順)。空なら計画の漏れ。"""

    function_id: str
    name: str
    milestones: tuple[str, ...]


def function_plans(plan: PlanModel, function_list: FunctionListModel | None) -> list[FunctionPlan]:
    """機能一覧の処理ごとに、割り当てたマイルストーンを導く(機能一覧の並び)。"""
    found: dict[str, list[str]] = {}
    for index, milestone in enumerate(plan.milestones):
        ids = list(milestone.function_ids)
        for task in milestone.tasks:
            ids += task.function_ids
        for function_id in dict.fromkeys(f.strip() for f in ids):
            found.setdefault(function_id, []).append(milestone_id(index))
    functions = function_list.functions if function_list is not None else []
    return [FunctionPlan(row.id, row.name, tuple(found.get(row.id, []))) for row in functions]
