"""要件定義書の再生成で、合意済みの行と利用者(1.2)を守る流れのテスト。

SUT: DocGeneratorService.generate の要件定義書の生成(`_generate_requirements`・`_agreed_elements`。
     app/services/doc_generator_service.py)、GeneratedDocumentRepository.create_version の
     `protection`とモデルの列、スキーマ GeneratedDocumentRead・RestoredRead(document_read)
ドライバ: 各テスト関数
スタブ: DB はインメモリ SQLite(`db_session`)を本物のリポジトリで使う。設計は段階1〜8 を承認済みに
した fixture(`create_impact_project`)。文書の生成の LLM は FakeLLM(合意済みの行を書き換えた・
落とした構造化出力を返し、プロンプトを記録する)
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.change_requests import NEW_TEXT, agreed_change_request_project
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.requirements import requirements_output

from app.documents.requirements.drafting import AGREED_MARK, RequirementsGenerationOutput
from app.documents.requirements.model import RequirementsModel
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.baseline import BaselineCreate
from app.schemas.document import RestoredRead
from app.services.baseline_service import BaselineService
from app.services.doc_generator_service import DocGeneratorService, document_read

_AT = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
_DIAGNOSIS = "## 自己診断\n最重要: 特になし"


async def _regenerate(
    session: AsyncSession,
    project: Project,
    output: RequirementsGenerationOutput,
    *,
    cr_number: int | None = None,
) -> FakeLLM:
    llm = FakeLLM(
        content_sequence=[_DIAGNOSIS, _DIAGNOSIS], structured_by_schema=upstream_outputs(output)
    )
    service = DocGeneratorService(session)
    status = await service.request_generation(
        project, start_from="requirements", cr_number=cr_number
    )
    await service.generate(
        project.id,
        project.user_id,
        status_before_generation=status,
        start_from="requirements",
        cr_number=cr_number,
        llm=llm,
    )
    return llm


async def _current(session: AsyncSession, project: Project) -> GeneratedDocument:
    document = await GeneratedDocumentRepository(session).get_current(
        project_id=project.id, doc_type="requirements"
    )
    assert document is not None and document.model is not None
    return document


def _rows(document: GeneratedDocument) -> list[tuple[str, str, str]]:
    model = RequirementsModel.model_validate(document.model)
    return [(r.id, r.text, r.source) for r in model.rows]


async def test_smoke_adopted_row_stays_adopted_after_regeneration(
    db_session: AsyncSession,
) -> None:
    """Phase 90-2 の再現: 根拠の観点が未決のまま「採用にする」で採用にした行は、CR を渡さない
    再生成で写すとき、推測に戻らない(合意済みの行に上限をかけ直さない)。"""
    project = await create_impact_project(db_session)
    documents = GeneratedDocumentRepository(db_session)
    document = await _current(db_session, project)
    model = RequirementsModel.model_validate(document.model)
    rows = [
        model.rows[0].model_copy(update={"source": "assumed", "points": ["core"]}),
        *model.rows[1:],
    ]
    assumed = model.model_copy(update={"rows": rows}).model_dump(mode="json")
    await documents.replace_model(document, content=document.content, model=assumed)
    await db_session.commit()
    await DocGeneratorService(db_session).adopt_requirement(project, "FR-001")

    output = requirements_output(prev_id="FR-001", text="予約を登録できるようにする")
    output.requirements[0].unchanged = True
    llm = await _regenerate(db_session, project, output)

    current = await _current(db_session, project)
    assert _rows(current)[0] == ("FR-001", "予約を登録できる", "adopted")
    assert current.protection == []  # 写した行は合意と同じなので、戻すものは無い
    prompt = str(llm.structured_messages[0][-1].content)
    assert f"FR-001|予約を登録できる|{AGREED_MARK}\n" in prompt
    assert "FR-002|予約の一覧を見られる\n" in prompt  # 合意していない行に印は無い


async def test_rewritten_and_dropped_rows_go_back_to_the_baseline(
    db_session: AsyncSession,
) -> None:
    """束 B-1 の後の、CR を渡さない再生成。LLM が FR-001 を書き換え、FR-002 と NFR-001 を落として
    も、合意の形に戻り、戻した要素が版に残る。合意の無い新しい行は残る。"""
    project = await create_impact_project(db_session)
    await BaselineService(db_session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )

    await _regenerate(db_session, project, requirements_output(prev_id="FR-001", text="書き換え"))

    current = await _current(db_session, project)
    assert _rows(current) == [
        ("FR-001", "予約を登録できる", "user"),
        ("FR-002", "予約の一覧を見られる", "user"),
        ("NFR-001", "本人の予約だけを見られる", "user"),
        ("NFR-002", "HTTPS", "assumed"),
    ]
    assert current.protection == [
        {"key": "req:FR-001", "reason": "changed"},
        {"key": "req:FR-002", "reason": "dropped"},
        {"key": "req:NFR-001", "reason": "dropped"},
    ]
    assert "予約の一覧を見られる" in current.content
    assert document_read(current).protection == [
        RestoredRead(key="req:FR-001", reason="changed"),
        RestoredRead(key="req:FR-002", reason="dropped"),
        RestoredRead(key="req:NFR-001", reason="dropped"),
    ]
    model = RequirementsModel.model_validate(current.model)
    assert model.next_seq["NFR"] == 3


async def test_change_request_targets_may_change(db_session: AsyncSession) -> None:
    """反映する CR の対象(FR-001)は書き換えてよい。対象の外の落ちた行は戻す。"""
    project = await agreed_change_request_project(db_session)

    await _regenerate(
        db_session, project, requirements_output(prev_id="FR-001", text=NEW_TEXT), cr_number=1
    )

    current = await _current(db_session, project)
    assert _rows(current)[:2] == [
        ("FR-001", NEW_TEXT, "assumed"),
        ("FR-002", "予約の一覧を見られる", "user"),
    ]
    assert [r["key"] for r in current.protection or []] == ["req:FR-002", "req:NFR-001"]


async def test_simple_mode_does_not_protect(db_session: AsyncSession) -> None:
    """簡易ドキュメントモードは合意を読まず守らない(版の`protection`は NULL)。"""
    project = await create_impact_project(db_session)
    await BaselineService(db_session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    project.mode = "simple"
    await db_session.commit()

    await _regenerate(db_session, project, requirements_output(prev_id="FR-001", text="書き換え"))

    current = await _current(db_session, project)
    assert [r[:2] for r in _rows(current)] == [("FR-001", "書き換え"), ("NFR-002", "HTTPS")]
    assert current.protection is None
    assert document_read(current).protection is None
