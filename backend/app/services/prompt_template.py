from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_template import PromptTemplate
from app.repositories.prompt_template import PromptTemplateRepository


class PromptTemplateService:
    """選択可能なプロンプトテンプレート(固定シードデータ)の参照を担当するサービス。
    CRUD機能は設けない(内部設計書3.2節⑤: テンプレートは固定シードデータのみ)。"""

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._prompt_templates = PromptTemplateRepository(session)

    async def list_all(self) -> list[PromptTemplate]:
        """選択可能なテンプレート一覧を名前順で取得する。"""
        return await self._prompt_templates.list_all_templates()
