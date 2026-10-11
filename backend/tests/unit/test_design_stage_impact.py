"""変更の影響の記録(段階の承認・文書の版の切り替え)と、確認済み・承認し直しの条件のテスト。

SUT: DesignStageService.approve(印の記録と承認を止める条件)/ record_document_change /
     review_marks / impact / stage_impact(app/services/design_stage_service.py)、
     ルート get_design_impact・review_impact_marks(app/api/routes/design_stages.py)、
     応答の形(app/schemas/design_stage.py)、DocGeneratorService.generate・
     restore_version の記録の呼び出し(app/services/doc_generator_service.py)、
     DesignStageUnreviewedImpactError(app/services/errors.py)
ドライバ: 各テスト関数
スタブ: DB はインメモリ SQLite(`db_session`)を本物のリポジトリで使う。文書の生成の LLM は
FakeLLM(構造化出力の見本を返す)。段階1〜8 は fixture が承認済み(写しあり)で直接作る
(承認の検証は既存のテストが確かめるため、ここでは印の記録だけを見る)。
"""

import copy

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project, impact_documents, impact_stages
from tests.fixtures.detailed_design import create_stage4_project, crud_model, er_model
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.requirements import requirements_output

from app.api.routes.design_stages import (
    get_design_impact,
    list_design_stages,
    review_impact_marks,
)
from app.detailed_design.impact import Mark, StageImpact
from app.models.design_stage import DesignStage
from app.models.project import Project
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.schemas.design_stage import ImpactReview
from app.services.design_stage_service import ER_SNAPSHOT_KEY, DesignStageService
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import DesignStageUnreviewedImpactError


async def _row(session: AsyncSession, project: Project, stage: int) -> DesignStage:
    row = await DesignStageRepository(session).get(project_id=project.id, stage=stage)
    assert row is not None
    return row


async def _impacts(session: AsyncSession, project: Project, stages: range) -> list[dict | None]:
    return [(await _row(session, project, stage)).impact for stage in stages]


async def _impact(session: AsyncSession, project: Project, stage: int) -> StageImpact:
    return StageImpact.from_dict((await _row(session, project, stage)).impact)


async def _edit_and_approve(
    session: AsyncSession, project: Project, stage: int, model: dict
) -> None:
    service = DesignStageService(session)
    version = (await _row(session, project, stage)).version
    await service.save(project, stage=stage, expected_version=version, model=model)
    await service.approve(project, stage=stage, expected_version=version + 1)


def _renamed_function() -> dict:
    model = copy.deepcopy(impact_stages()[1])
    model["functions"][1]["name"] = "予約を検索する"
    return model


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


async def test_smoke_approving_edited_stage_records_marks_downstream(
    db_session: AsyncSession,
) -> None:
    project = await create_impact_project(db_session)

    await _edit_and_approve(db_session, project, 1, _renamed_function())

    stage5 = await _impact(db_session, project, 5)
    assert [m.key for m in stage5.marks] == ["proc:F-02"]
    assert stage5.marks[0].sources == ("stage:1",)
    assert [(c.source, c.change.key, c.change.change) for c in stage5.changes] == [
        ("stage:1", "fn:F-02", "content")
    ]
    row1 = await _row(db_session, project, 1)
    assert row1.approved_model == _renamed_function()
    assert row1.impact is None


# --- 記録 ---


async def test_first_approval_without_snapshot_records_nothing(db_session: AsyncSession) -> None:
    """写しの無い段階(移行前に承認の後で編集した行など)は差分を取らない。"""
    project = await create_impact_project(db_session)
    row1 = await _row(db_session, project, 1)
    row1.approved_model = None
    await db_session.commit()

    await _edit_and_approve(db_session, project, 1, _renamed_function())

    assert await _impacts(db_session, project, range(2, 9)) == [None] * 7


async def test_marks_are_recorded_only_on_stages_approved_once(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    row5 = await _row(db_session, project, 5)
    row5.approved_version = None
    await db_session.commit()

    await _edit_and_approve(db_session, project, 1, _renamed_function())

    assert (await _row(db_session, project, 5)).impact is None
    assert (await _impact(db_session, project, 7)).marks


async def test_table_definition_change_in_er_is_compared_with_stage3_snapshot(
    db_session: AsyncSession,
) -> None:
    """ER は段階の model の外にあるので、段階3の写しに承認したときのテーブル定義を添え、列を足す
    などのテーブルの内容の変更も差分に入れる。"""
    project = await create_stage4_project(db_session)
    row3 = await _row(db_session, project, 3)
    assert row3.approved_model == {**crud_model(), ER_SNAPSHOT_KEY: _definitions(er_model())}
    er = await UmlDiagramRepository(db_session).get_by_subject(
        project_id=project.id, notation="er", subject=""
    )
    assert er is not None
    changed = copy.deepcopy(er_model())
    changed["elements"][0]["columns"].append({"name": "note", "type": "TEXT"})
    er.semantic_model = changed
    await db_session.commit()

    await _edit_and_approve(db_session, project, 3, crud_model())

    stage3 = await _impact(db_session, project, 3)
    assert [(m.key, m.sources) for m in stage3.marks] == [
        ("crud:F-01×reservations", ("stage:3",))
    ]
    assert [c.change.key for c in stage3.changes] == ["table:reservations"]
    assert (await _row(db_session, project, 3)).approved_model == {
        **crud_model(),
        ER_SNAPSHOT_KEY: _definitions(changed),
    }


def _definitions(model: dict) -> dict:
    return {
        e["name"]: {"columns": [_column(c) for c in e["columns"]], "description": ""}
        for e in model["elements"]
    }


def _column(column: dict) -> dict:
    defaults = {
        "is_primary_key": False,
        "is_foreign_key": False,
        "nullable": True,
        "constraints": "",
        "description": "",
    }
    return {**defaults, **column}


async def test_restoring_document_records_marks_from_previously_shown_version(
    db_session: AsyncSession,
) -> None:
    project = await create_impact_project(db_session)
    changed = copy.deepcopy(impact_documents()["requirements"])
    changed["rows"][2]["text"] = "本人の予約だけを見られる(管理者は全件)"
    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="requirements", content="# 改訂", model=changed
    )
    await db_session.commit()

    # 版2(表示中)から版1へ戻す: NFR-001 の内容が変わる → 横断事項「認証」→ 手順書
    await DocGeneratorService(db_session).restore_version(project.id, "requirements", 1)

    stage7 = await _impact(db_session, project, 7)
    stage8 = await _impact(db_session, project, 8)
    assert [m.key for m in stage7.marks] == ["cc:認証"]
    assert [m.sources for m in stage7.marks] == [("doc:requirements",)]
    assert {m.key for m in stage8.marks} == {"unit:U-002", "unit:U-003"}


async def test_generating_documents_records_marks_for_detailed_project(
    db_session: AsyncSession,
) -> None:
    project = await create_impact_project(db_session)
    fake_llm = FakeLLM(
        content_sequence=["## 自己診断\n最重要: 特になし"],
        structured_by_schema=upstream_outputs(
            requirements_output(prev_id="FR-001", text="予約を登録し、確認メールを送る")
        ),
    )

    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=fake_llm)

    stage1 = await _impact(db_session, project, 1)
    assert "fn:F-01" in {m.key for m in stage1.marks}
    assert "doc:requirements" in {s for m in stage1.marks for s in m.sources}


async def test_document_change_is_ignored_for_simple_project_or_missing_model(
    db_session: AsyncSession,
) -> None:
    project = await create_impact_project(db_session)
    service = DesignStageService(db_session)
    before = impact_documents()["requirements"]

    await service.record_document_change(project, "requirements", None, before)
    project.mode = "simple"
    await service.record_document_change(project, "requirements", before, {"rows": []})

    assert await _impacts(db_session, project, range(1, 9)) == [None] * 8


# --- 確認済みと承認し直し ---


async def test_unreviewed_marks_block_approval_until_reviewed(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    await _edit_and_approve(db_session, project, 1, _renamed_function())
    service = DesignStageService(db_session)
    row5 = await _row(db_session, project, 5)
    version = row5.version
    await service.save(project, stage=5, expected_version=version, model=row5.model or {})

    with pytest.raises(DesignStageUnreviewedImpactError):
        await service.approve(project, stage=5, expected_version=version + 1)

    await service.review_marks(project, stage=5, keys=["proc:F-02"], reviewed=True)
    assert (await _impact(db_session, project, 5)).reviewed_count == 1
    approved = await service.approve(project, stage=5, expected_version=version + 1)
    assert approved.state == "approved"
    assert (await _row(db_session, project, 5)).impact is None


async def test_own_marks_do_not_block_next_approval(db_session: AsyncSession) -> None:
    """同じ段階の中の依存で自分に付いた印(段階4のモジュールの依存など)は、次の承認を止めず、承認で
    消える。自分の承認で付く印は純粋関数のテスト(test_impact_marks.py)が確かめるので、ここでは行に
    自分の印を置いて承認する(段階4は fixture に構成図が無く、承認の検証を通らないため段階7で
    見る)。"""
    project = await create_impact_project(db_session)
    own = Mark("task:U-003", "M-01-T03", 7, ("task:U-002", "task:U-003"), "r", ("stage:7",))
    row7 = await _row(db_session, project, 7)
    row7.impact = StageImpact(marks=(own,)).to_dict()
    await db_session.commit()

    await _edit_and_approve(db_session, project, 7, impact_stages()[7])

    assert (await _row(db_session, project, 7)).impact is None


async def test_review_marks_can_reopen_and_ignores_unknown_keys(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    await _edit_and_approve(db_session, project, 1, _renamed_function())
    service = DesignStageService(db_session)

    await service.review_marks(project, stage=5, keys=["proc:F-02", "nope"], reviewed=True)
    await service.review_marks(project, stage=5, keys=["proc:F-02"], reviewed=False)

    assert (await _impact(db_session, project, 5)).open_count == 1


# --- API(変更の影響の取得・確認済みの切り替え) ---


async def test_routes_return_recorded_marks_counts_and_regeneration_targets(
    db_session: AsyncSession,
) -> None:
    project = await create_impact_project(db_session)
    await _edit_and_approve(db_session, project, 1, _renamed_function())

    impact = await get_design_impact(db_session, project)
    reviewed = await review_impact_marks(
        5, ImpactReview(keys=["proc:F-02"]), db_session, project
    )
    stages = await list_design_stages(db_session, project)

    assert [item.stage for item in impact.stages] == [1, 2, 3, 4, 5, 6, 7, 8]
    stage5 = impact.stages[4]
    assert [(m.key, m.path, m.pending) for m in stage5.marks] == [
        ("proc:F-02", ["fn:F-02", "proc:F-02"], False)
    ]
    assert stage5.changes[0].source == "stage:1"
    assert {t.key for t in impact.regeneration_targets} >= {"proc:F-02", "unit:U-003"}
    assert all(t.stage in (5, 6, 8) for t in impact.regeneration_targets)
    assert (reviewed.open, reviewed.reviewed) == (0, 1)
    assert (stages[4].impact_open, stages[4].impact_reviewed) == (0, 1)
    assert stages[6].impact_open > 0


async def test_unapproved_edit_shows_pending_marks_without_recording(
    db_session: AsyncSession,
) -> None:
    """承認の後に編集した段階の差分は、記録せずに pending の印として返す。"""
    project = await create_impact_project(db_session)
    service = DesignStageService(db_session)
    await service.save(project, stage=1, expected_version=1, model=_renamed_function())

    impact = await service.impact(project)

    stage5 = impact.stages[4]
    assert [(m.key, m.pending) for m in stage5.marks] == [("proc:F-02", True)]
    assert [(c.key, c.pending) for c in stage5.changes] == [("fn:F-02", True)]
    assert (stage5.open, stage5.reviewed) == (0, 0)
    assert (await _row(db_session, project, 5)).impact is None


async def test_simple_project_has_only_stage8_without_marks(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    project.mode = "simple"

    impact = await DesignStageService(db_session).impact(project)

    assert [(item.stage, item.marks) for item in impact.stages] == [(8, [])]
