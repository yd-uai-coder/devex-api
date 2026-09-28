import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_template import PromptTemplate
from app.repositories.prompt_template import PromptTemplateRepository


async def test_list_all_templates_orders_by_name(db_session: AsyncSession) -> None:
    repo = PromptTemplateRepository(db_session)
    db_session.add(
        PromptTemplate(name="Webアプリケーション標準", target_type="Web", system_prompt="x")
    )
    db_session.add(PromptTemplate(name="API向け", target_type="API", system_prompt="y"))
    await db_session.flush()

    result = await repo.list_all_templates()

    assert [t.name for t in result] == ["API向け", "Webアプリケーション標準"]


async def test_get_by_id_returns_none_for_missing_template(db_session: AsyncSession) -> None:
    repo = PromptTemplateRepository(db_session)

    result = await repo.get_by_id(uuid.uuid4())

    assert result is None
