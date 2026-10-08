"""E2E用の偽LLM(`E2eFakeLLM`)の、簡易ドキュメントモードの段階8の出力の契約テスト。

ブラウザの E2E(devex-ui の `e2e/devex-flow.spec.ts`)と同じく、偽LLM の4文書から段階8を開き、
実装計画書の WBS の全単位の手順書を生成する。E2E は生成までで、このテストは続けて承認と実装手順書の
zip まで通す。偽LLM の出力が本物の解析(WBS・内部設計書)と検証を通ることを、ブラウザを使わずに
固定する。

SUT: E2eFakeLLM の4文書と段階8の構造化出力(app/ai/llm/fake.py)と、それを受ける生成・検証・承認・
     出力の経路(DesignStageGenerationService・DesignStageService・DetailedDesignExportService)
ドライバ: テスト関数(画面の操作と同じ順に、サービスのメソッドを呼ぶ)
スタブ: なし ── 偽LLM は検証される側(出力の出どころ)。DB はインメモリ SQLite(`db_session`)。
"""

import io
import zipfile

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.simple_procedure import create_simple_procedure_project

from app.ai.llm.fake import E2eFakeLLM
from app.detailed_design import has_errors, validate_stage
from app.services.design_stage_generation_service import DesignStageGenerationService
from app.services.design_stage_service import DesignStageService
from app.services.detailed_design_export_service import DetailedDesignExportService
from app.services.doc_generator_service import _DOC_TYPE_PROMPTS


async def _fake_documents(llm: E2eFakeLLM) -> dict[str, str]:
    """偽LLM が、4文書それぞれのプロンプトに返す本文(文書の種類 → 本文)。"""
    documents: dict[str, str] = {}
    for doc_type, prompt in _DOC_TYPE_PROMPTS.items():
        reply = await llm.ainvoke([SystemMessage(content=prompt), HumanMessage(content="dummy")])
        documents[doc_type] = str(reply.content)
    return documents


async def test_e2e_fake_outputs_pass_simple_stage8_and_bundle(db_session: AsyncSession) -> None:
    llm = E2eFakeLLM()
    project = await create_simple_procedure_project(db_session, **await _fake_documents(llm))
    stages = DesignStageService(db_session)

    # 段階8だけが開き、作業単位は偽の実装計画書の WBS から読める(文書の指摘は無い)
    [stage8] = await stages.list_stages(project)
    assert (stage8.stage, stage8.mode, stage8.is_open, stage8.issues) == (8, "simple", True, [])

    # 全単位を生成する(画面の「生成」と同じく、受け付けてから実行する)
    units = ["M-01-T01", "M-01-T02"]
    generation = DesignStageGenerationService(db_session)
    await generation.request_generation(project, stage=8, unit_ids=units)
    await generation.execute(
        project_id=project.id, user_id=project.user_id, stage=8, unit_ids=units, llm=llm
    )
    drafted = await stages.read(project.id, 8)
    assert drafted.generation_status == "completed", drafted.generation_error
    assert drafted.model is not None
    assert [u["unit_id"] for u in drafted.model["units"]] == units
    assert {f["fix_document"] for u in drafted.model["units"] for f in u["findings"]} == {
        "internal_design"
    }

    # 検証のエラーが無いことを確かめてから承認し、実装手順書の zip を作る
    row, _, sources = await stages.stage_view(project, 8)
    assert row is not None
    issues = validate_stage(8, row.model, sources)
    assert not has_errors(issues), [i for i in issues if i.severity == "error"]
    await stages.approve(project, stage=8, expected_version=row.version)
    bundle = await DetailedDesignExportService(db_session).bundle_procedure(project)
    names = zipfile.ZipFile(io.BytesIO(bundle.content)).namelist()
    assert {"index.md", "implementation_procedure.html", "ai/M-01-T02.md"} <= set(names)
