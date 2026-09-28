from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.prompt_templates import list_prompt_templates
from app.models.prompt_template import PromptTemplate
from app.models.user import User


async def test_list_prompt_templates_returns_all_templates(db_session: AsyncSession) -> None:
    user = User(email="viewer@example.com", hashed_password="x")
    db_session.add(user)
    db_session.add(
        PromptTemplate(name="Webアプリケーション標準", target_type="Web", system_prompt="x")
    )
    db_session.add(PromptTemplate(name="API向け", target_type="API", system_prompt="y"))
    await db_session.flush()

    result = await list_prompt_templates(db_session, user)

    assert {t.name for t in result} == {"Webアプリケーション標準", "API向け"}


async def test_list_prompt_templates_returns_empty_list_when_none_seeded(
    db_session: AsyncSession,
) -> None:
    user = User(email="viewer-empty@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()

    result = await list_prompt_templates(db_session, user)

    assert result == []
