"""E2E用の偽LLM(`E2eFakeLLM`)の出力で、変更の影響(承認時の写し・差分・印・確認済み・承認し直し)を
通す契約テスト。

ブラウザの E2E(devex-ui の `e2e/detailed-design-flow.spec.ts` の終わり)と同じく、段階1〜8 を
承認した後に段階1の処理を1つ直して承認し直し、下流の段階に付いた印を確認済みにしてから承認し直す。
あわせて、同じ出力での文書の再生成(差分の再生成・単位のキーの引き継ぎ)が印を増やさないこと、
簡易モードで再生成が要件を変えたときに、その要件を担う単位にだけ印が付くことを確かめる。
偽LLM の出力が本物の要素のグラフ・たどり方・承認の条件を通ることを、ブラウザを使わずに固定する。

SUT: E2eFakeLLM の出力(app/ai/llm/fake.py)と、それを受ける変更の影響の経路
     (DesignStageService.save・approve・impact・review_marks、DocGeneratorService.generate の
     印の記録)
ドライバ: テスト関数(画面の操作と同じ順に、サービスのメソッドを呼ぶ)
スタブ: なし ── 偽LLM は検証される側(出力の出どころ)。DB はインメモリ SQLite(`db_session`)。
"""

import copy

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.external_design import e2e_fake_upstream
from tests.fixtures.fake_llm_flow import (
    FAKE_UNITS,
    approve_stage,
    create_fake_detailed_project,
    generate_and_approve_stages_1_to_7,
    generate_stage,
)
from tests.fixtures.simple_documents import e2e_fake_simple_documents
from tests.fixtures.simple_procedure import create_simple_procedure_project

from app.ai.llm.fake import E2eFakeLLM
from app.models.project import Project
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.design_stage import DesignImpactRead
from app.services.design_stage_service import DesignStageService
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import DesignStageUnreviewedImpactError

# 段階1で直す処理(F-01 予約を登録する)の印が届く、段階ごとの要素のキー。F-02(一覧)には届かない
_F01_MARKS: dict[int, list[str]] = {
    2: ["summary:F-01"],
    3: ["crud:F-01×reservations"],
    4: ["module:app/api/routes/reservations.py", "module:app/services/reservation.py"],
    5: ["proc:F-01"],
    6: ["logic:app/services/reservation.py::ReservationService.create"],
    7: ["task:U-002"],
    8: ["unit:U-002"],
}


def _marks(impact: DesignImpactRead) -> dict[int, list[str]]:
    """印のある段階 → 印のキー(印の無い段階は除く)。"""
    return {s.stage: [m.key for m in s.marks] for s in impact.stages if s.marks}


async def _approved_detailed_project(session: AsyncSession, llm: E2eFakeLLM) -> Project:
    """段階1〜8 をすべて承認した詳細設計モードのプロジェクト(段階8は全単位の手順書)。"""
    project = await create_fake_detailed_project(session, llm)
    await generate_and_approve_stages_1_to_7(session, project, llm)
    await generate_stage(session, project, 8, llm, unit_ids=FAKE_UNITS)
    await approve_stage(session, project, 8)
    return project


async def test_smoke_editing_function_marks_downstream_until_reviewed(
    db_session: AsyncSession,
) -> None:
    llm = E2eFakeLLM()
    project = await _approved_detailed_project(db_session, llm)
    stages = DesignStageService(db_session)
    # 承認で写しが撮られ、まだ印は無い
    stage1 = await stages.read(project.id, 1)
    assert stage1.model is not None
    assert _marks(await stages.impact(project)) == {}

    # 段階1の F-01 の要約を直して保存すると、承認前は pending の印として見える
    edited = copy.deepcopy(stage1.model)
    edited["functions"][0]["summary"] += "(取消の期限も検証する)"
    saved = await stages.save(project, stage=1, expected_version=stage1.version, model=edited)
    assert saved.version is not None
    pending = await stages.impact(project)
    assert _marks(pending) == _F01_MARKS
    assert all(m.pending for s in pending.stages for m in s.marks)

    # 承認し直すと印が記録され、変わったものは F-01、再生成の候補は段階5・6・8
    await stages.approve(project, stage=1, expected_version=saved.version)
    recorded = await stages.impact(project)
    assert _marks(recorded) == _F01_MARKS
    assert not any(m.pending for s in recorded.stages for m in s.marks)
    assert {c.key for s in recorded.stages for c in s.changes} == {"fn:F-01"}
    assert [(t.stage, t.key) for t in recorded.regeneration_targets] == [
        (5, "proc:F-01"),
        (6, "logic:app/services/reservation.py::ReservationService.create"),
        (8, "unit:U-002"),
    ]

    # 印が未確認のままの段階は承認できない。確認済みにすると承認できる
    with pytest.raises(DesignStageUnreviewedImpactError):
        await approve_stage(db_session, project, 2)
    await stages.review_marks(project, stage=2, keys=_F01_MARKS[2], reviewed=True)
    await approve_stage(db_session, project, 2)
    after = await stages.impact(project)
    assert 2 not in _marks(after)
    assert _marks(after)[8] == ["unit:U-002"]


async def test_regenerating_documents_with_same_output_adds_no_marks(
    db_session: AsyncSession,
) -> None:
    llm = E2eFakeLLM()
    project = await _approved_detailed_project(db_session, llm)
    stages = DesignStageService(db_session)

    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=llm)

    # 文書の版は新しくなる(段階は「古い」になる)が、内容が同じなので印は付かない
    assert {s.stage: s.state for s in await stages.list_stages(project)}[1] == "outdated"
    assert _marks(await stages.impact(project)) == {}


async def test_simple_regeneration_keeps_unit_keys_and_marks_units_of_changed_requirement(
    db_session: AsyncSession,
) -> None:
    llm = E2eFakeLLM()
    documents = dict(await e2e_fake_upstream(llm))
    documents.update(await e2e_fake_simple_documents(llm))
    # 最初の版の FR-001 の文を変えておく(再生成で偽LLM の文に戻る = 要件が変わる)
    requirements = copy.deepcopy(documents["requirements"][1])
    requirements["rows"][0]["text"] = "[E2E Fake] 備品を予約できる(旧)"
    documents["requirements"] = (documents["requirements"][0], requirements)
    project = await create_simple_procedure_project(
        db_session,
        models={k: model for k, (_, model) in documents.items()},
        **{k: content for k, (content, _) in documents.items()},
    )
    units = ["M-01-T01", "M-01-T02"]
    await generate_stage(db_session, project, 8, llm, unit_ids=units)
    await approve_stage(db_session, project, 8)
    stages = DesignStageService(db_session)

    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=llm)

    # 単位のキーは前の版から引き継ぐ
    plan = await GeneratedDocumentRepository(db_session).get_current(
        project_id=project.id, doc_type="implementation_plan"
    )
    assert plan is not None and plan.model is not None
    assert [t["key"] for m in plan.model["milestones"] for t in m["tasks"]] == ["U-001", "U-002"]
    # FR-001 を担う単位(M-01-T02 = U-002)にだけ印が付き、確認済みにするまで承認できない
    impact = await stages.impact(project)
    assert _marks(impact) == {8: ["unit:U-002"]}
    assert [c.key for s in impact.stages for c in s.changes] == ["req:FR-001"]
    with pytest.raises(DesignStageUnreviewedImpactError):
        await approve_stage(db_session, project, 8)
    await stages.review_marks(project, stage=8, keys=["unit:U-002"], reviewed=True)
    await approve_stage(db_session, project, 8)
    assert _marks(await stages.impact(project)) == {}
