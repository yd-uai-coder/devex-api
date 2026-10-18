"""テスト仕様書の model の組み立てと、API → 処理 → 単位の対応のテスト。

SUT は`app/documents/test_spec/assemble.py`の`build_model`と`links.py`の`api_links`・
`simple_links`・`detailed_links`、ドライバはこのテスト。スタブ不要 ── 対象が純粋関数で、下書きと
上流の model だけから決まるため。
"""

from tests.fixtures.spec_documents import (
    CREATE_API,
    LIST_API,
    spec_draft,
    spec_external,
    spec_requirements,
)

from app.detailed_design.function_list import FunctionListModel, FunctionRow
from app.detailed_design.plan import Milestone, PlanModel, PlanTask
from app.documents.implementation_plan.model import ImplementationPlanModel
from app.documents.internal_design.model import DataFlowRow, InternalDesignModel
from app.documents.test_spec import (
    ApiLink,
    TestSpecModel,
    api_key,
    api_links,
    build_model,
    case_id,
    detailed_links,
    simple_links,
)

PLAN = PlanModel(
    milestones=[
        Milestone(
            name="予約",
            tasks=[
                PlanTask(key="U-001", kind="base", title="準備"),
                PlanTask(key="U-002", title="予約", function_ids=["F-01", "DF-1"]),
            ],
        )
    ]
)


CREATE_KEY = "POST /api/v1/reservations"
LIST_KEY = "GET /api/v1/reservations"


def _model(links: dict[str, ApiLink] | None = None) -> TestSpecModel:
    return build_model(
        spec_draft(),
        requirements=spec_requirements(),
        external=spec_external(),
        links=links or {},
        inputs={"doc:requirements": 1},
    )


def test_case_id_and_api_key():
    assert case_id("integration", 1) == "IT-001"
    assert case_id("system", 12) == "ST-012"
    assert api_key("post /api/v1/x/{project_id}/") == "POST /api/v1/x/{}"
    assert api_key("SCR-001 → SCR-002") is None


def test_ids_are_numbered_per_level():
    model = _model()
    assert [c.id for c in model.integration] == ["IT-001", "IT-002", "IT-003"]
    assert [c.id for c in model.system] == ["ST-001"]


def test_target_and_ids_are_normalized():
    first = _model().integration[0]
    assert first.target == CREATE_API
    assert first.req_ids == ["FR-001"]
    assert first.screens == ["SCR-001"]


def test_snapshots_and_inputs_are_copied():
    model = _model()
    assert model.known_req_ids == ["FR-001", "FR-002", "FR-003", "NFR-001"]
    assert model.must_fr_ids == ["FR-001", "FR-002"]
    assert model.req_priorities == {"FR-001": "Must", "FR-002": "Must", "FR-003": "Should"}
    assert model.known_apis[0] == LIST_API
    assert model.known_screens == ["SCR-001", "SCR-002"]
    assert model.inputs == {"doc:requirements": 1}


def test_links_fill_functions_and_units_of_integration_tests_only():
    links = {CREATE_KEY: ApiLink(("F-01",), ("M-01-T02",))}
    model = _model(links)
    assert (model.integration[0].functions, model.integration[0].units) == (["F-01"], ["M-01-T02"])
    assert model.integration[2].functions == []
    assert model.system[0].functions == []


def test_api_links_follow_trigger_and_unit_functions():
    links = api_links([("F-01", "POST /api/v1/reservations"), ("F-02", "毎日のバッチ")], PLAN)
    assert links == {CREATE_KEY: ApiLink(("F-01",), ("M-01-T02",))}
    assert api_links([("F-01", LIST_API)], None)[LIST_KEY].units == ()


def test_simple_and_detailed_links():
    internal = InternalDesignModel(dataflows=[DataFlowRow(id="DF-1", trigger=CREATE_API)])
    plan_doc = ImplementationPlanModel(milestones=PLAN.milestones)
    expected = ApiLink(("DF-1",), ("M-01-T02",))
    assert simple_links(internal, plan_doc)[CREATE_KEY] == expected
    assert simple_links(None, plan_doc) == {}
    row = FunctionRow(id="F-01", name="予約", trigger=CREATE_API)
    functions = FunctionListModel(functions=[row])
    assert detailed_links(functions, PLAN)[CREATE_KEY].functions == ("F-01",)
    assert detailed_links(None, PLAN) == {}
