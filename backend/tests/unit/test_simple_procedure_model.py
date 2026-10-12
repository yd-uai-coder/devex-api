"""簡易モードの段階8の土台が、内部設計書・実装計画書の model を読むこと(純粋関数)。

SUT: app/detailed_design/procedure_basis.procedure_basis(簡易モード)と
     app/detailed_design/simple_procedure.book_from_model。ドライバ: このテスト。
スタブ不要 ── 対象が純粋(副作用なし)で、LLM・DB を呼ばないため。model は見本の構造化出力から
生成と同じく組み立て、同じ model の md を解析した結果と比べる(model の無い以前の版との互換)。
"""

from tests.fixtures.simple_documents import implementation_plan_output, internal_design_output

from app.detailed_design.procedure_basis import ProcedureBasis, procedure_basis
from app.detailed_design.simple_procedure import book_from_model, parse_internal_design
from app.documents.implementation_plan import ImplementationPlanModel, render_implementation_plan
from app.documents.implementation_plan import build_model as build_plan_model
from app.documents.implementation_plan.drafting import to_draft as to_plan_draft
from app.documents.internal_design import (
    ColumnRow,
    InternalDesignModel,
    TableDef,
    build_model,
    render_internal_design,
)
from app.documents.internal_design.drafting import to_draft

EXTERNAL_MD = """## 2.6 API一覧
| メソッド | パス | 概要 | 関連画面 |
|---|---|---|---|
| POST | /api/v1/reservations | 予約 | SCR-001 |
"""


def _models() -> tuple[InternalDesignModel, ImplementationPlanModel]:
    internal = build_model(to_draft(internal_design_output(architecture="## 方針\n3層")), None)
    internal = internal.model_copy(
        update={"tables": [TableDef(name="reservations", columns=[ColumnRow(name="id")])]}
    )
    plan = build_plan_model(to_plan_draft(implementation_plan_output()), None)
    return internal, plan


def _basis(internal: InternalDesignModel, plan: ImplementationPlanModel, *, with_models: bool):
    documents = {
        "external_design": EXTERNAL_MD,
        "internal_design": render_internal_design(internal),
        "implementation_plan": render_implementation_plan(plan),
    }
    models = {
        "internal_design": internal.model_dump(mode="json"),
        "implementation_plan": plan.model_dump(mode="json"),
    }
    return procedure_basis("simple", {}, documents, models if with_models else None)


def _without_keys(basis: ProcedureBasis) -> list[tuple[str, dict]]:
    return [(u.unit_id, u.task.model_dump(exclude={"key"})) for u in basis.units]


def test_book_from_model_matches_the_md_parser() -> None:
    internal, _ = _models()

    from_model = book_from_model(internal, EXTERNAL_MD)
    from_md = parse_internal_design(render_internal_design(internal), EXTERNAL_MD)

    assert from_model == from_md
    assert from_model.dataflows["DF-1"].tables == ("reservations",)
    assert from_model.architecture == "**方針**\n3層"


def test_simple_basis_reads_models_like_the_md() -> None:
    internal, plan = _models()

    from_model = _basis(internal, plan, with_models=True)
    from_md = _basis(internal, plan, with_models=False)

    assert _without_keys(from_model) == _without_keys(from_md)
    assert from_model.book == from_md.book
    assert (from_model.environment, from_model.rules) == (from_md.environment, from_md.rules)
    assert from_model.wbs_issues == from_md.wbs_issues == ()
    # model の版は単位の安定キーを持つ(md の版は単位の ID を代わりに使う)
    assert [u.key for u in from_model.units] == ["U-001"]
    assert [u.key for u in from_md.units] == ["M-01-T01"]


def test_simple_basis_falls_back_to_md_for_broken_models_and_reports_no_tasks() -> None:
    internal, plan = _models()
    documents = {"implementation_plan": render_implementation_plan(plan)}

    broken = procedure_basis("simple", {}, documents, {"implementation_plan": {"milestones": 1}})
    empty = procedure_basis(
        "simple", {}, documents, {"implementation_plan": ImplementationPlanModel().model_dump()}
    )

    assert [u.unit_id for u in broken.units] == ["M-01-T01"]
    assert empty.units == []
    assert [i.code for i in empty.wbs_issues] == ["WBS_MISSING"]
