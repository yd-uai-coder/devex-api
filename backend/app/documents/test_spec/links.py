"""API → 処理 → 作業単位の対応(純粋関数)。テスト仕様書の「処理・単位」の列をコードが埋める。

AI には書かせない(設計で決まっている対応を書き直させないため)。処理のトリガーの API と、単位の
処理から引く:

- 簡易ドキュメントモード: 内部設計書の処理別データフロー(`DF-1`。トリガーが API)→ 実装計画書の
  タスク(処理の欄がデータフローの ID)。
- 詳細設計モード: 段階1の処理(`F-01`。トリガーが API)→ 段階7の単位(処理の欄が処理ID)。
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.detailed_design.api_list import trigger_key
from app.detailed_design.function_list import FunctionListModel
from app.detailed_design.plan import PlanModel
from app.detailed_design.procedure_doc import plan_units
from app.documents.implementation_plan.model import ImplementationPlanModel
from app.documents.internal_design.model import InternalDesignModel


@dataclass(frozen=True)
class ApiLink:
    """API 1つの処理ID と単位の ID。"""

    functions: tuple[str, ...] = ()
    units: tuple[str, ...] = ()


def api_links(
    triggers: Iterable[tuple[str, str]], plan: PlanModel | None
) -> dict[str, ApiLink]:
    """(処理ID, トリガー)の並びと計画から、API の照合のキー(`endpoint_key`)→ 処理・単位。
    API でないトリガーは読み飛ばす。計画が無ければ単位は空。"""
    functions: dict[str, list[str]] = {}
    for function_id, trigger in triggers:
        key = trigger_key(trigger)
        if key is not None and function_id not in functions.setdefault(key, []):
            functions[key].append(function_id)
    units = plan_units(plan) if plan is not None else []
    return {
        key: ApiLink(
            tuple(ids),
            tuple(u.unit_id for u in units if any(i in u.task.function_ids for i in ids)),
        )
        for key, ids in functions.items()
    }


def simple_links(
    internal: InternalDesignModel | None, plan: ImplementationPlanModel | None
) -> dict[str, ApiLink]:
    """簡易ドキュメントモードの対応(データフロー → 実装計画書のタスク)。"""
    if internal is None:
        return {}
    triggers = ((f.id, f.trigger) for f in internal.dataflows)
    return api_links(triggers, plan.to_plan() if plan is not None else None)


def detailed_links(
    functions: FunctionListModel | None, plan: PlanModel | None
) -> dict[str, ApiLink]:
    """詳細設計モードの対応(段階1の処理 → 段階7の単位)。"""
    if functions is None:
        return {}
    return api_links(((f.id, f.trigger) for f in functions.functions), plan)
