"""要件 → 処理 → 作業単位の追跡(純粋関数)。

docs/internal_design.md 3.3節「6. ヒアリングと上流の構造化」。

要件ID(`FR-001`)は要件定義書の model が正本。下流は次のように要件ID を持つ:

- 詳細設計モード: 段階1の処理の`req_ids`。作業単位(段階7)の要件は、単位の処理から導く
  (単位に要件を持たせない。処理を付け替えれば要件も付いてくる)。
- 簡易ドキュメントモード: 実装計画書の WBS のタスクの「要件」欄(`PlanTask.req_ids`)。

要件の照合には2つの入り口がある。段階1は入力の外部設計書の model に写した要件ID
(`known_req_ids`・`must_fr_ids`)を読む(段階1の入力を増やさないため)。段階8と手順書は、
要件の本文・優先度も要るので要件定義書の model を読む。どちらも model が無い・要件ID が無い
文書(Phase 36・37 より前に生成した版)では照合しない(`RequirementIndex.available`)。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.detailed_design.api_list import endpoint_key
from app.detailed_design.function_list import FunctionRow
from app.detailed_design.procedure_doc import PlanUnit
from app.documents.requirements.model import (
    RequirementRow,
    RequirementsModel,
    parse_requirement_id,
    requirement_id,
)


@dataclass(frozen=True)
class RequirementIndex:
    """要件の照合に使う値。`rows`は要件定義書の model から作ったときだけ持つ。"""

    known_ids: tuple[str, ...] = ()
    must_fr_ids: tuple[str, ...] = ()
    rows: tuple[RequirementRow, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.known_ids)

    def row(self, req_id: str) -> RequirementRow | None:
        return next((r for r in self.rows if r.id == req_id), None)


@dataclass(frozen=True)
class TraceRow:
    """「要件との対応」の1行。`functions`は処理ID(簡易モードは単位の処理 DF)。"""

    req_id: str
    priority: str
    text: str
    functions: tuple[str, ...]
    units: tuple[str, ...]


def index_from_requirements(model: Mapping[str, Any] | None) -> RequirementIndex:
    """要件定義書の model(無い・形が違えば照合しない空の索引)。"""
    if not model:
        return RequirementIndex()
    try:
        parsed = RequirementsModel.model_validate(model)
    except ValidationError:
        return RequirementIndex()
    return RequirementIndex(
        known_ids=tuple(r.id for r in parsed.rows),
        must_fr_ids=tuple(r.id for r in parsed.rows if _is_must_fr(r)),
        rows=tuple(parsed.rows),
    )


def index_from_external_design(model: Mapping[str, Any] | None) -> RequirementIndex:
    """外部設計書の model に写した要件ID(生成した時点の要件定義書のもの)。"""
    if not model:
        return RequirementIndex()
    return RequirementIndex(
        known_ids=tuple(_strings(model.get("known_req_ids"))),
        must_fr_ids=tuple(_strings(model.get("must_fr_ids"))),
    )


def api_req_ids(external_design: Mapping[str, Any] | None) -> dict[str, list[str]]:
    """外部設計書の model の 2.6 の行 → 要件ID(キーは`endpoint_key`。段階1の下書きが写す)。"""
    apis = (external_design or {}).get("apis")
    result: dict[str, list[str]] = {}
    for api in apis if isinstance(apis, list) else []:
        if isinstance(api, Mapping) and api.get("method") and api.get("path"):
            key = endpoint_key(str(api["method"]), str(api["path"]))
            result.setdefault(key, _clean(_strings(api.get("req_ids"))))
    return result


def normalize_req_id(value: str) -> str | None:
    """`fr-1`・` FR-001 `を`FR-001`にそろえる。要件ID の形でなければ None。"""
    parsed = parse_requirement_id(value.strip().upper())
    return requirement_id(*parsed) if parsed is not None else None


def function_req_ids(functions: Iterable[FunctionRow]) -> dict[str, list[str]]:
    """処理ID → 要件ID(詳細設計モードの単位の要件を導く元)。"""
    return {row.id: _clean(row.req_ids) for row in functions}


def unit_req_ids(
    units: Sequence[PlanUnit], functions: Iterable[FunctionRow] | None = None
) -> dict[str, list[str]]:
    """作業単位 → 要件ID。`functions`(段階1の処理)を渡せば単位の処理から導き(詳細設計モード)、
    渡さなければ単位自身の`req_ids`(簡易モードの WBS)を使う。"""
    if functions is None:
        return {u.unit_id: _clean(u.task.req_ids) for u in units}
    by_function = function_req_ids(functions)
    return {
        u.unit_id: _clean(
            req for f in u.task.function_ids for req in by_function.get(f.strip(), [])
        )
        for u in units
    }


def requirement_trace(
    index: RequirementIndex,
    units: Sequence[PlanUnit],
    unit_reqs: Mapping[str, Sequence[str]],
    functions: Iterable[FunctionRow] | None = None,
) -> list[TraceRow]:
    """機能要件(FR。Won't を除く)ごとの処理と作業単位。

    処理は、詳細設計モードなら段階1で要件を持つ処理、簡易モード(`functions`無し)なら要件を持つ
    単位の処理(DF)。要件定義書の model が無ければ空。"""
    function_rows = list(functions) if functions is not None else None
    trace: list[TraceRow] = []
    for row in index.rows:
        if row.kind != "FR" or row.priority == "Won't":
            continue
        unit_ids = tuple(u.unit_id for u in units if row.id in unit_reqs.get(u.unit_id, ()))
        if function_rows is not None:
            function_ids = tuple(f.id for f in function_rows if row.id in _clean(f.req_ids))
        else:
            function_ids = tuple(
                dict.fromkeys(
                    f.strip()
                    for u in units
                    if u.unit_id in unit_ids
                    for f in u.task.function_ids
                    if f.strip()
                )
            )
        trace.append(TraceRow(row.id, row.priority or "", row.text, function_ids, unit_ids))
    return trace


def constraint_rows(index: RequirementIndex) -> list[RequirementRow]:
    """非機能要件(NFR)と制約(CON)の行。手順書の「実装前提・制約」に載せる。"""
    return [r for r in index.rows if r.kind in ("NFR", "CON")]


def _is_must_fr(row: RequirementRow) -> bool:
    return row.kind == "FR" and row.priority == "Must"


def _strings(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _clean(values: Iterable[str]) -> list[str]:
    """空白を除き、空と重複を捨てる(並びは保つ)。"""
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))
