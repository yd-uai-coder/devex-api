from scripts.seed import SEED_PROMPT_TEMPLATES, seed_prompt_templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.prompt_template import PromptTemplateRepository


async def test_seed_prompt_templates_creates_all_fixed_templates(db_session: AsyncSession) -> None:
    await seed_prompt_templates(db_session)

    result = await PromptTemplateRepository(db_session).list_all_templates()

    assert {t.name for t in result} == {data["name"] for data in SEED_PROMPT_TEMPLATES}


async def test_seed_prompt_templates_is_idempotent(db_session: AsyncSession) -> None:
    await seed_prompt_templates(db_session)
    await seed_prompt_templates(db_session)

    result = await PromptTemplateRepository(db_session).list_all_templates()

    assert len(result) == len(SEED_PROMPT_TEMPLATES)
