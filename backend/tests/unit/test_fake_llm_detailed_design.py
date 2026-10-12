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

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm_flow import (
    FAKE_UNITS,
    approve_stage,
    create_fake_detailed_project,
    generate_and_approve_stages_1_to_7,
    generate_stage,
)

from app.ai.llm.fake import E2eFakeLLM
from app.services.design_stage_service import DesignStageService
from app.services.detailed_design_export_service import DetailedDesignExportService


async def test_e2e_fake_outputs_pass_stages_1_to_8_and_bundle(db_session: AsyncSession) -> None:
    llm = E2eFakeLLM()
    project = await create_fake_detailed_project(db_session, llm)
    stages = DesignStageService(db_session)

    # 段階1〜7 を画面と同じ順に生成・承認する(段階ごとの手順は tests/fixtures/fake_llm_flow.py)
    await generate_and_approve_stages_1_to_7(db_session, project, llm)

    # 段階1: 機能一覧(F-01・F-02、グループ reservations)。要件ID は外部設計書の 2.6 から写り、
    # 要件の追跡の指摘は無い
    stage1 = await stages.read(project.id, 1)
    assert stage1.model is not None
    assert [f["req_ids"] for f in stage1.model["functions"]] == [["FR-001"], ["FR-002"]]
    assert stage1.issues == []

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
    units = FAKE_UNITS
    await generate_stage(db_session, project, 8, llm, unit_ids=units)
    drafted = await stages.read(project.id, 8)
    assert drafted.model is not None
    assert [u["unit_id"] for u in drafted.model["units"]] == units
    await approve_stage(db_session, project, 8)
    procedure = await DetailedDesignExportService(db_session).bundle_procedure(project)
    archive = zipfile.ZipFile(io.BytesIO(procedure.content))
    names = archive.namelist()
    assert {"index.md", "implementation_procedure.html", "ai/M-01-T02.md"} <= set(names)
    # 要件との対応: Must の FR-001 は F-01 と、その単位が担う
    index = archive.read("index.md").decode()
    assert "## 4. 要件との対応" in index
    assert "| FR-001 | Must | [E2E Fake] 備品を予約できる | F-01 | M-01-T02 |" in index
