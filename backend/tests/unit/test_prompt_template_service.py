from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_template import PromptTemplate
from app.services.prompt_template import PromptTemplateService


async def test_list_all_returns_templates_sorted_by_name(db_session: AsyncSession) -> None:
    db_session.add(
        PromptTemplate(name="Webアプリケーション標準", target_type="Web", system_prompt="x")
    )
    db_session.add(PromptTemplate(name="API向け", target_type="API", system_prompt="y"))
    await db_session.flush()
    service = PromptTemplateService(db_session)

    result = await service.list_all()

    assert [t.name for t in result] == ["API向け", "Webアプリケーション標準"]


async def test_list_all_returns_empty_list_when_none_seeded(db_session: AsyncSession) -> None:
    service = PromptTemplateService(db_session)

    result = await service.list_all()

    assert result == []
