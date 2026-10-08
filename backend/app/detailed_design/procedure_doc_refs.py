"""段階8 単位が参照する設計の展開(純粋関数)。

docs/internal_design.md 3.3節「5. 実装手順書」。

`unit_refs`(procedure_doc.py)が導いた参照を、承認済みの設計の該当箇所の md に展開する。展開した
中身は保存しない。手順書の下書きの入力と、画面の単位の詳細(参照の API)が同じ関数を使う。
AI 向けの出力も同じ関数を使い、どこでも同じ中身にする(作成方針17章)。

- 書式は詳細設計書の 05・06 と同じ表(`procedure_table`・`logic_spec`)にする。段階5の手順には、
  05 と同じシーケンス図(Mermaid。`sequence_block`)を添え、画面の単位の詳細には SVG も渡す。
- 段階7の横断事項(07章)と開発環境は、単位の参照とは別の共通の節として、どの単位にも添える。
- 解決できない参照(設計に無い)は展開しない(None)。何が足りないかは段階8の検証が指摘する。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.detailed_design.document.markdown import (
    logic_spec,
    md_table,
    procedure_table,
    sequence_block,
)
from app.detailed_design.document.views import logic_ids
from app.detailed_design.function_list import FunctionListModel, FunctionRow
from app.detailed_design.logic import LOGIC_STAGE, LogicModel, LogicRow, logic_key
from app.detailed_design.plan import PLAN_STAGE, PlanModel
from app.detailed_design.procedure import PROCEDURE_STAGE, Procedure, ProcedureModel
from app.detailed_design.procedure_doc import (
    DesignRef,
    DesignRefKind,
    PlanUnit,
    design_index,
    unit_refs,
)
from app.detailed_design.sequence import SequenceDiagram, to_mermaid, to_sequence
from app.detailed_design.sequence_svg import to_sequence_svg
from app.detailed_design.structure import STRUCTURE_STAGE, ModuleListModel, ModuleRow


@dataclass(frozen=True)
class DesignBook:
    """展開に使う、承認済みの段階1・4〜7の内容。

    - `functions`: 処理ID → 機能一覧の行(段階1)
    - `procedures`: 処理ID → 手順(段階5)
    - `logics`: `logic_key` → 関数の詳細(段階6)。`logic_ids`は`logic_key` → L-ID
    - `modules`: パス → モジュール一覧の行(段階4)
    - `plan`: 横断事項と実装計画(段階7)
    """

    functions: Mapping[str, FunctionRow]
    procedures: Mapping[str, Procedure]
    logics: Mapping[str, LogicRow]
    logic_ids: Mapping[str, str]
    modules: Mapping[str, ModuleRow]
    plan: PlanModel


def design_book(stages: Mapping[int, Mapping[str, Any]]) -> DesignBook:
    """入力の段階(段階番号 → 承認済みの`model`)から、展開に使う内容を集める。"""
    functions = FunctionListModel.model_validate(stages.get(1) or {})
    procedures = ProcedureModel.model_validate(stages.get(PROCEDURE_STAGE) or {})
    logics = LogicModel.model_validate(stages.get(LOGIC_STAGE) or {})
    modules = ModuleListModel.model_validate(stages.get(STRUCTURE_STAGE) or {})
    return DesignBook(
        functions={row.id: row for row in functions.functions},
        procedures={p.function_id.strip(): p for p in procedures.procedures},
        logics={logic_key(row.module, row.function): row for row in logics.logics},
        logic_ids=logic_ids(logics),
        modules={row.path.strip(): row for row in modules.modules},
        plan=PlanModel.model_validate(stages.get(PLAN_STAGE) or {}),
    )


@dataclass(frozen=True)
class ExpandedRef:
    """展開した参照1つ。`markdown`は該当箇所の md(解決できない参照は None)。`svg`と`mermaid`は
    段階5の手順のシーケンス図の SVG と Mermaid の本文(手順の参照だけ。図にする参加者が無ければ
    None)。`mermaid`は、参照を ID だけで書く手順書の md に図だけを載せるために使う。"""

    kind: DesignRefKind
    key: str
    resolved: bool
    via: str | None
    label: str
    markdown: str | None
    svg: str | None = None
    mermaid: str | None = None


@dataclass(frozen=True)
class UnitContext:
    """単位1つ分の、手順書を作る・読むための材料。`crosscutting`・`environment`は段階7の
    07章と開発環境の md(書かれていなければ空)。"""

    unit: PlanUnit
    refs: tuple[ExpandedRef, ...]
    crosscutting: str
    environment: str


def ref_label(ref: DesignRef, book: DesignBook) -> str:
    """参照の見出し(`段階5 F-01 予約を登録する`・`段階6 L-02 create(`app/x.py`)`・
    `段階4 `app/x.py``)。"""
    if ref.kind == "procedure":
        function = book.functions.get(ref.key)
        return f"段階5 {ref.key} {function.name if function else ''}".rstrip()
    if ref.kind == "logic":
        module, _, name = ref.key.partition("::")
        lid = book.logic_ids.get(ref.key)
        return f"段階6 {f'{lid} ' if lid else ''}{name}(`{module}`)"
    return f"段階4 `{ref.key}`"


def ref_sequence(ref: DesignRef, book: DesignBook) -> SequenceDiagram | None:
    """段階5の手順の参照のシーケンス図(手順の参照でない・解決できないときは None)。依存先は
    承認済みの段階4から。"""
    procedure = book.procedures.get(ref.key) if ref.kind == "procedure" and ref.resolved else None
    if procedure is None:
        return None
    dependencies = {path: list(row.depends_on) for path, row in book.modules.items()}
    return to_sequence(procedure, dependencies)


def expand_ref(ref: DesignRef, book: DesignBook) -> str | None:
    """参照1つを、設計の該当箇所の md にする(解決できない参照は None)。"""
    if not ref.resolved:
        return None
    if ref.kind == "procedure":
        procedure = book.procedures.get(ref.key)
        if procedure is None:
            return None
        lines = [f"### {ref_label(ref, book)}", ""]
        function = book.functions.get(ref.key)
        if function is not None and function.trigger:
            lines += [f"トリガー: {function.trigger}", ""]
        lines += procedure_table(procedure, book.logic_ids)
        if procedure.note.strip():
            lines += ["", f"注記: {procedure.note.strip()}"]
        diagram = ref_sequence(ref, book)
        block = sequence_block(diagram) if diagram is not None else []
        if block:
            lines += ["", *block]
        return "\n".join(lines)
    if ref.kind == "logic":
        row = book.logics.get(ref.key)
        if row is None:
            return None
        lines = [f"### {ref_label(ref, book)}", ""]
        if ref.via:
            lines += [f"呼ばれる手順: {ref.via}", ""]
        return "\n".join(lines + logic_spec(row))
    module = book.modules.get(ref.key)
    if module is None:
        return None
    depends = ", ".join(module.depends_on) or "なし"
    return (
        f"- 段階4 `{module.path}`({module.layer or '—'}): "
        f"{module.responsibility or '—'} / 依存先: {depends}"
    )


def _drawable(ref: DesignRef, book: DesignBook) -> SequenceDiagram | None:
    diagram = ref_sequence(ref, book)
    return diagram if diagram is not None and diagram.participants else None


def _expanded(ref: DesignRef, book: DesignBook) -> ExpandedRef:
    diagram = _drawable(ref, book)
    return ExpandedRef(
        kind=ref.kind,
        key=ref.key,
        resolved=ref.resolved,
        via=ref.via,
        label=ref_label(ref, book),
        markdown=expand_ref(ref, book),
        svg=to_sequence_svg(diagram) if diagram is not None else None,
        mermaid=to_mermaid(diagram) if diagram is not None else None,
    )


def crosscutting_section(plan: PlanModel) -> str:
    """段階7の 07 横断事項の md(行が無ければ空)。"""
    if not plan.crosscutting:
        return ""
    lines = md_table(
        ["項目", "方針", "関わるファイル(例)"],
        [[row.topic, row.policy, ", ".join(row.modules)] for row in plan.crosscutting],
    )
    return "\n".join(["### 07章 横断事項", "", *lines])


def environment_section(plan: PlanModel) -> str:
    """段階7の開発環境の md(書かれていなければ空)。"""
    text = plan.environment.strip()
    return f"### 段階7 開発環境\n\n{text}" if text else ""


def unit_context(unit: PlanUnit, stages: Mapping[int, Mapping[str, Any]]) -> UnitContext:
    """単位の参照を導いて展開し、共通の節と合わせる。"""
    book = design_book(stages)
    refs = tuple(
        _expanded(ref, book) for ref in unit_refs(unit.task, design_index(stages))
    )
    return UnitContext(
        unit=unit,
        refs=refs,
        crosscutting=crosscutting_section(book.plan),
        environment=environment_section(book.plan),
    )
