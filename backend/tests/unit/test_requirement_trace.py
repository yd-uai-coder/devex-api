"""要件 → 処理 → 作業単位の追跡(純粋関数)のテスト。

SUT は`app/detailed_design/requirement_trace.py`、ドライバはこのテスト。
スタブ不要 ── 入力は文書の model(dict)・段階1の処理・段階7の作業単位だけで、
DB や LLM を呼ばないため。
"""

from tests.fixtures.simple_procedure import REQUIREMENTS_MODEL

from app.detailed_design.function_list import FunctionRow
from app.detailed_design.plan import Milestone, PlanModel, PlanTask
from app.detailed_design.procedure_doc import plan_units
from app.detailed_design.requirement_trace import (
    TraceRow,
    api_req_ids,
    constraint_rows,
    index_from_external_design,
    index_from_requirements,
    normalize_req_id,
    requirement_trace,
    unit_req_ids,
)


def _function(function_id: str, *req_ids: str) -> FunctionRow:
    return FunctionRow(id=function_id, name=function_id, req_ids=list(req_ids))


def _plan(*tasks: PlanTask) -> PlanModel:
    return PlanModel(milestones=[Milestone(name="M", tasks=list(tasks))])


def test_detailed_trace_derives_units_from_functions() -> None:
    """統合スモーク: 詳細設計モードは、単位の要件を単位の処理から導き、FR ごとの処理と単位を並べる
    (Won't の要件は載せない)。"""
    index = index_from_requirements(REQUIREMENTS_MODEL)
    functions = [_function("F-01", "FR-001"), _function("F-02", "FR-001", "FR-002")]
    units = plan_units(
        _plan(
            PlanTask(title="登録", function_ids=["F-01"]),
            PlanTask(title="一覧", function_ids=["F-02"]),
        )
    )

    reqs = unit_req_ids(units, functions)
    trace = requirement_trace(index, units, reqs, functions)

    assert reqs == {"M-01-T01": ["FR-001"], "M-01-T02": ["FR-001", "FR-002"]}
    assert trace == [
        TraceRow("FR-001", "Must", "予約の登録", ("F-01", "F-02"), ("M-01-T01", "M-01-T02")),
        TraceRow("FR-002", "Should", "予約の一覧", ("F-02",), ("M-01-T02",)),
    ]


def test_simple_trace_reads_units_and_their_dataflows() -> None:
    """簡易モードは単位自身の要件(WBS)を使い、処理の列には単位の DF を出す。"""
    index = index_from_requirements(REQUIREMENTS_MODEL)
    units = plan_units(
        _plan(
            PlanTask(title="登録", function_ids=["DF-1"], req_ids=["FR-001"]),
            PlanTask(kind="base", title="準備"),
        )
    )

    trace = requirement_trace(index, units, unit_req_ids(units))

    assert [(t.req_id, t.functions, t.units) for t in trace] == [
        ("FR-001", ("DF-1",), ("M-01-T01",)),
        ("FR-002", (), ()),
    ]


def test_indexes_and_constraints() -> None:
    """要件定義書の model と外部設計書の写しの索引。model が無ければ照合しない。"""
    from_requirements = index_from_requirements(REQUIREMENTS_MODEL)
    from_external = index_from_external_design(
        {"known_req_ids": ["FR-001", "NFR-001"], "must_fr_ids": ["FR-001"]}
    )

    assert from_requirements.must_fr_ids == ("FR-001",)
    assert from_requirements.row("NFR-001") is not None
    assert (from_external.known_ids, from_external.must_fr_ids, from_external.rows) == (
        ("FR-001", "NFR-001"),
        ("FR-001",),
        (),
    )
    assert [r.id for r in constraint_rows(from_requirements)] == ["NFR-001", "CON-001"]
    assert not index_from_requirements(None).available
    assert not index_from_requirements({"rows": "壊れた形"}).available
    assert not index_from_external_design({}).available


def test_api_req_ids_and_normalize() -> None:
    """2.6 の行は`endpoint_key`で引ける(同じ API の2行目は捨てる)。要件ID は形をそろえる。"""
    external = {
        "apis": [
            {"method": "post", "path": "/api/v1/reservations/", "req_ids": ["FR-001", " "]},
            {"method": "POST", "path": "/api/v1/reservations", "req_ids": ["FR-002"]},
            {"method": "", "path": "/x"},
        ]
    }

    assert api_req_ids(external) == {"POST /api/v1/reservations": ["FR-001"]}
    assert api_req_ids(None) == {}
    assert normalize_req_id(" fr-1 ") == "FR-001"
    assert normalize_req_id("XX-1") is None
