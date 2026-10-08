"""E2E用の偽LLM(`E2eFakeLLM`)の、詳細設計モードの段階1〜8の出力の契約テスト。

ブラウザの E2E(devex-ui の `e2e/detailed-design-flow.spec.ts`)と同じ順に、偽LLM で段階1〜7を
生成し、図を配置して承認し、段階を承認して、zip を作る。続けて段階8の全単位の手順書を生成し、
承認して、実装手順書の zip を作る(E2E は段階8の生成までで、承認と zip はこのテストだけが通す)。
偽LLM の出力が本物の検証(段階の `validate_stage`・図の構造検証)を通ることを、ブラウザを使わずに
固定する。E2E が落ちたとき、原因が画面にあるのか偽LLM の出力にあるのかを、このテストで
切り分けられる。

SUT: E2eFakeLLM の構造化出力(app/ai/llm/fake.py)と、それを受ける生成・検証・承認・出力の経路
     (DesignStageGenerationService・DesignStageService・UmlDiagramService・
     DetailedDesignExportService)
ドライバ: テスト関数(画面の操作と同じ順に、サービスのメソッドを呼ぶ)
スタブ: なし ── 偽LLM はスタブとして差し込むのではなく、検証される側(出力の出どころ)。DB は
インメモリ SQLite(`db_session`)、図の配置はレイアウトエンジンで実際に計算する。
"""

import io
import zipfile

from langchain_core.messages import SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project

from app.ai.llm.fake import E2eFakeLLM
from app.detailed_design import has_errors, validate_stage
from app.models.project import Project
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.design_stage_generation_service import DesignStageGenerationService
from app.services.design_stage_service import DesignStageService
from app.services.detailed_design_export_service import DetailedDesignExportService
from app.services.uml_diagram_service import UmlDiagramService

# 段階ごとに、生成の後に承認する図の記法(段階2 = DFD、3 = ER、4 = 構成図)
_DIAGRAMS_OF_STAGE: dict[int, str] = {2: "dfd", 3: "er", 4: "component"}

# 段階6で詳細を書く関数(偽LLM の段階5の手順の、呼び出し先と関数)
_LOGIC_TARGET = ("app/services/reservation.py", "ReservationService.create")


async def _fake_document(llm: E2eFakeLLM, marker: str) -> str:
    """偽LLM が、その文書のプロンプト(見出しの印)に返す本文。"""
    return str((await llm.ainvoke([SystemMessage(content=marker)])).content)


async def _create_project(session: AsyncSession, llm: E2eFakeLLM) -> Project:
    """ヒアリング後の詳細設計モードのプロジェクト(要件定義・外部設計は偽LLM の本文)。"""
    project = await create_detailed_project(session, with_documents=False)
    documents = GeneratedDocumentRepository(session)
    for doc_type, marker in (
        ("requirements", "# 1. 要件定義書"),
        ("external_design", "# 2. 外部設計書"),
    ):
        await documents.create_version(
            project_id=project.id, doc_type=doc_type, content=await _fake_document(llm, marker)
        )
    await session.commit()
    return project


async def _generate(
    session: AsyncSession, project: Project, stage: int, llm: E2eFakeLLM, **targets
) -> None:
    """画面の「生成」と同じく、受け付けてから実行する(実行はバックグラウンドの代わりに直接)。"""
    service = DesignStageGenerationService(session)
    await service.request_generation(project, stage=stage, **targets)
    await service.execute(
        project_id=project.id, user_id=project.user_id, stage=stage, llm=llm, **targets
    )
    read = await DesignStageService(session).read(project.id, stage)
    assert read.generation_status == "completed", (stage, read.generation_error)


async def _approve_diagrams(session: AsyncSession, project: Project, notation: str) -> None:
    """画面で図を開いたときの自動レイアウトと、図の「承認」。"""
    diagrams = UmlDiagramService(session)
    for diagram in await UmlDiagramRepository(session).list_for_project(project.id):
        if diagram.notation != notation:
            continue
        laid_out = await diagrams.compute_layout(project_id=project.id, diagram_id=diagram.id)
        await diagrams.approve(
            project_id=project.id, diagram_id=diagram.id, expected_version=laid_out.version
        )


async def _approve_stage(session: AsyncSession, project: Project, stage: int) -> None:
    """検証のエラーが無いことを確かめてから、段階を承認する(失敗したときにコードが分かるよう、
    承認の前に検証の結果を見る)。"""
    stages = DesignStageService(session)
    row, _, sources = await stages.stage_view(project, stage)
    assert row is not None
    issues = validate_stage(stage, row.model, sources)
    assert not has_errors(issues), (stage, [i for i in issues if i.severity == "error"])
    await stages.approve(project, stage=stage, expected_version=row.version)


async def test_e2e_fake_outputs_pass_stages_1_to_8_and_bundle(db_session: AsyncSession) -> None:
    llm = E2eFakeLLM()
    project = await _create_project(db_session, llm)
    stages = DesignStageService(db_session)

    # 段階1: 機能一覧(F-01・F-02、グループ reservations)
    await _generate(db_session, project, 1, llm)
    await _approve_stage(db_session, project, 1)

    # 段階2: グループを選んで保存してから生成する(初回は行が無いので version なしで保存)
    await stages.save(
        project,
        stage=2,
        expected_version=None,
        model={"dfd_groups": ["reservations"], "summaries": []},
    )
    await _generate(db_session, project, 2, llm)

    for stage in (2, 3, 4):
        if stage != 2:
            await _generate(db_session, project, stage, llm)
        await _approve_diagrams(db_session, project, _DIAGRAMS_OF_STAGE[stage])
        await _approve_stage(db_session, project, stage)

    # 段階5: 処理を選んで保存し、手順の無い処理を生成する
    await stages.save(
        project,
        stage=5,
        expected_version=None,
        model={"procedures": [{"function_id": "F-01"}, {"function_id": "F-02"}]},
    )
    await _generate(db_session, project, 5, llm)
    await _approve_stage(db_session, project, 5)

    # 段階6: 関数を1つ選んで保存し、その関数を生成する
    module, function = _LOGIC_TARGET
    await stages.save(
        project,
        stage=6,
        expected_version=None,
        model={"logics": [{"module": module, "function": function}]},
    )
    await _generate(db_session, project, 6, llm, logics=[_LOGIC_TARGET])
    await _approve_stage(db_session, project, 6)

    # 段階7: 横断事項と実装計画
    await _generate(db_session, project, 7, llm)
    await _approve_stage(db_session, project, 7)

    # 段階8(実装手順書)は開いているが、まだ生成も保存もしていない
    assert [s.state for s in await stages.list_stages(project)] == ["approved"] * 7 + [
        "not_started"
    ]
    bundle = await DetailedDesignExportService(db_session).bundle(project)
    names = zipfile.ZipFile(io.BytesIO(bundle.content)).namelist()
    assert {
        "detailed_design.html",
        "detailed_design.md",
        "implementation_plan.html",
        "implementation_plan.md",
    } <= set(names)
    assert any(name.startswith("diagrams/") for name in names)

    # 段階8: 段階7の全単位(M-01-T01〜T03)の手順書を生成して承認し、実装手順書の zip を作る
    units = ["M-01-T01", "M-01-T02", "M-01-T03"]
    await _generate(db_session, project, 8, llm, unit_ids=units)
    drafted = await stages.read(project.id, 8)
    assert drafted.model is not None
    assert [u["unit_id"] for u in drafted.model["units"]] == units
    await _approve_stage(db_session, project, 8)
    procedure = await DetailedDesignExportService(db_session).bundle_procedure(project)
    names = zipfile.ZipFile(io.BytesIO(procedure.content)).namelist()
    assert {"index.md", "implementation_procedure.html", "ai/M-01-T02.md"} <= set(names)
