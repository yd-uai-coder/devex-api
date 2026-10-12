"""E2E用の偽LLM(`E2eFakeLLM`)で、詳細設計モードの段階を画面と同じ順に生成・承認する手順。

契約テスト(段階1〜8 の出力・変更の影響)が同じ流れを使う。画面の操作と同じく、生成は受け付けて
から実行し、図は自動レイアウトしてから承認し、段階は検証のエラーが無いことを確かめてから承認する。
"""

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project
from tests.fixtures.external_design import e2e_fake_upstream

from app.ai.llm.fake import E2eFakeLLM
from app.detailed_design import has_errors, validate_stage
from app.models.project import Project
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.design_stage_generation_service import DesignStageGenerationService
from app.services.design_stage_service import DesignStageService
from app.services.uml_diagram_service import UmlDiagramService

# 段階ごとに、生成の後に承認する図の記法(段階2 = DFD、3 = ER、4 = 構成図)
DIAGRAMS_OF_STAGE: dict[int, str] = {2: "dfd", 3: "er", 4: "component"}

# 段階6で詳細を書く関数(偽LLM の段階5の手順の、呼び出し先と関数)
LOGIC_TARGET = ("app/services/reservation.py", "ReservationService.create")

# 段階7の全単位(偽LLM の実装計画の作業単位)
FAKE_UNITS = ["M-01-T01", "M-01-T02", "M-01-T03"]


async def create_fake_detailed_project(session: AsyncSession, llm: E2eFakeLLM) -> Project:
    """ヒアリング後の詳細設計モードのプロジェクト(要件定義・外部設計は偽LLM の構造化出力から
    model → md。段階1は 2.6 の API 一覧と、外部設計書の model の要件ID を読む)。"""
    project = await create_detailed_project(session, with_documents=False)
    documents = GeneratedDocumentRepository(session)
    for doc_type, (content, model) in (await e2e_fake_upstream(llm)).items():
        await documents.create_version(
            project_id=project.id, doc_type=doc_type, content=content, model=model
        )
    await session.commit()
    return project


async def generate_stage(
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


async def approve_diagrams(session: AsyncSession, project: Project, notation: str) -> None:
    """画面で図を開いたときの自動レイアウトと、図の「承認」。"""
    diagrams = UmlDiagramService(session)
    for diagram in await UmlDiagramRepository(session).list_for_project(project.id):
        if diagram.notation != notation:
            continue
        laid_out = await diagrams.compute_layout(project_id=project.id, diagram_id=diagram.id)
        await diagrams.approve(
            project_id=project.id, diagram_id=diagram.id, expected_version=laid_out.version
        )


async def approve_stage(session: AsyncSession, project: Project, stage: int) -> None:
    """検証のエラーが無いことを確かめてから、段階を承認する(失敗したときにコードが分かるよう、
    承認の前に検証の結果を見る)。"""
    stages = DesignStageService(session)
    row, _, sources = await stages.stage_view(project, stage)
    assert row is not None
    issues = validate_stage(stage, row.model, sources)
    assert not has_errors(issues), (stage, [i for i in issues if i.severity == "error"])
    await stages.approve(project, stage=stage, expected_version=row.version)


async def generate_and_approve_stages_1_to_7(
    session: AsyncSession, project: Project, llm: E2eFakeLLM
) -> None:
    """段階1〜7 を、画面と同じ順に生成・承認する(段階2・5・6 は対象を選んで保存してから生成)。"""
    stages = DesignStageService(session)
    await generate_stage(session, project, 1, llm)
    await approve_stage(session, project, 1)

    # 段階2: グループを選んで保存してから生成する(初回は行が無いので version なしで保存)
    await stages.save(
        project,
        stage=2,
        expected_version=None,
        model={"dfd_groups": ["reservations"], "summaries": []},
    )
    await generate_stage(session, project, 2, llm)
    for stage in (2, 3, 4):
        if stage != 2:
            await generate_stage(session, project, stage, llm)
        await approve_diagrams(session, project, DIAGRAMS_OF_STAGE[stage])
        await approve_stage(session, project, stage)

    # 段階5: 処理を選んで保存し、手順の無い処理を生成する
    await stages.save(
        project,
        stage=5,
        expected_version=None,
        model={"procedures": [{"function_id": "F-01"}, {"function_id": "F-02"}]},
    )
    await generate_stage(session, project, 5, llm)
    await approve_stage(session, project, 5)

    # 段階6: 関数を1つ選んで保存し、その関数を生成する
    module, function = LOGIC_TARGET
    await stages.save(
        project,
        stage=6,
        expected_version=None,
        model={"logics": [{"module": module, "function": function}]},
    )
    await generate_stage(session, project, 6, llm, logics=[LOGIC_TARGET])
    await approve_stage(session, project, 6)

    # 段階7: 横断事項と実装計画
    await generate_stage(session, project, 7, llm)
    await approve_stage(session, project, 7)
