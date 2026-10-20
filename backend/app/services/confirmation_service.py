import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.confirmations import ConfirmationItem, confirmation_items, item_target
from app.models.project import Project
from app.repositories.confirmation import ConfirmationRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.hearing_point import HearingPointRepository
from app.schemas.confirmation import (
    ConfirmationListRead,
    ConfirmationUpdate,
    confirmation_list_read,
)
from app.services.detailed_mode import require_detailed
from app.services.doc_generator_service import DocGeneratorService, parse_requirements_model
from app.services.errors import ConfirmationItemNotFoundError


class ConfirmationService:
    """確認事項の一覧(docs/external_design.md 2.15節)。一覧は今の要件定義書と観点の表から毎回
    組み立て(app/confirmations。LLM なし)、利用者が決めた欄(決める機会・状態・メモ)を重ねる。
    詳細設計モードだけ。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._records = ConfirmationRepository(session)
        self._documents = GeneratedDocumentRepository(session)
        self._hearing_points = HearingPointRepository(session)

    async def view(self, project: Project) -> ConfirmationListRead:
        """確認事項の一覧と推測の採用率。簡易ドキュメントモードは DesignStagesNotAvailableError。"""
        require_detailed(project, "Confirmations")
        items = await self._items(project)
        return confirmation_list_read(items, await self._records.list_for_project(project.id))

    async def update(
        self,
        project: Project,
        key: str,
        payload: ConfirmationUpdate,
        *,
        updated_by: uuid.UUID | None,
    ) -> ConfirmationListRead:
        """項目1件の欄を変え、作り直した一覧を返す。

        今の一覧にある推測の要件・推測の利用者を確認済みにすると、「採用にする」と同じく文書の
        行を採用にし、合意を記録する(同じトランザクション)。観点・却下・今の一覧に無い項目は
        欄だけ変える(観点の状態はチャットと観点の表で決める)。キーが今の一覧にも保存した行にも
        無ければ ConfirmationItemNotFoundError。"""
        require_detailed(project, "Confirmations")
        items = {item.key: item for item in await self._items(project)}
        item = items.get(key)
        if item is None:
            record = await self._records.find_one(project_id=project.id, key=key)
            if record is None:
                raise ConfirmationItemNotFoundError(f"Confirmation item {key} not found")
            item = ConfirmationItem(record.key, record.kind, record.label)  # type: ignore[arg-type]
        fields = payload.model_dump(exclude_unset=True)
        if fields.get("status") == "confirmed" and key in items:
            documents = DocGeneratorService(self._session)
            if item.kind == "requirement":
                await documents.apply_adopt_requirement(
                    project, item_target(key), recorded_by=updated_by
                )
            elif item.kind == "user":
                await documents.apply_adopt_user(project, item_target(key), recorded_by=updated_by)
        await self._records.save(project.id, item, **fields, updated_by=updated_by)
        await self._session.commit()
        return await self.view(project)

    async def _items(self, project: Project) -> list[ConfirmationItem]:
        document = await self._documents.get_current(
            project_id=project.id, doc_type="requirements"
        )
        model = parse_requirements_model(document.model if document else None)
        return confirmation_items(model, await self._hearing_points.load_states(project.id))
