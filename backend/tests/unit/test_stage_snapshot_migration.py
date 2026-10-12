"""段階の承認時の写しのデータ移行の変換と、承認で写しを撮ることのテスト。

SUT: snapshot_of(alembic/versions/e5f6a7b8c9d1_add_stage_snapshot.py)、
     DesignStageService.approve の写し(app/services/design_stage_service.py)、
     DesignStage.approved_model・impact の列(app/models/design_stage.py)
ドライバ: 各テスト関数(移行ファイルは importlib で読み込み、純粋関数を直接呼ぶ)
スタブ: 移行の変換はスタブ不要 ── 純粋関数(副作用なし)で DB を呼ばないため。承認の写しは
インメモリ SQLite(`db_session`)を本物のリポジトリで使う(LLM は呼ばない)。
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project, function_list_model

from app.models.design_stage import DesignStage
from app.repositories.design_stage import DesignStageRepository
from app.services.design_stage_service import DesignStageService

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_PATH = _VERSIONS / "e5f6a7b8c9d1_add_stage_snapshot.py"
MODEL = function_list_model()


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("add_stage_snapshot", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_only_for_approved_and_unchanged_rows() -> None:
    migration = _migration()

    assert migration.snapshot_of("approved", MODEL, 2, 2) == MODEL
    # 承認の後に編集した行は、承認したときの内容が残っていない
    assert migration.snapshot_of("reviewing", MODEL, 3, 2) is None
    assert migration.snapshot_of("draft", MODEL, 1, None) is None
    assert migration.snapshot_of("approved", None, 1, 1) is None


async def test_approve_takes_snapshot_and_edit_keeps_it(db_session: AsyncSession) -> None:
    """承認で今の model を写しにし、その後の編集では写しを変えない(次の承認で比べる起点)。"""
    project = await create_detailed_project(db_session)
    service = DesignStageService(db_session)
    await service.save(project, stage=1, expected_version=None, model=MODEL)
    await service.approve(project, stage=1, expected_version=1)

    await service.save(project, stage=1, expected_version=1, model={"functions": []})

    row = await DesignStageRepository(db_session).get(project_id=project.id, stage=1)
    assert isinstance(row, DesignStage)
    assert row.approved_model == MODEL
    assert row.model == {"functions": []}
    assert row.impact is None
