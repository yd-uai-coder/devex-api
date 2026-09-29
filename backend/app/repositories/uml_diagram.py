import uuid

from app.models.uml_diagram import UmlDiagram
from app.repositories.base import CRUDRepository


class UmlDiagramRepository(CRUDRepository[UmlDiagram]):
    """UmlDiagramモデルに対する永続化操作をまとめるリポジトリ。楽観ロックの版チェックや
    生成トリガーとしてのプレースホルダー生成といったビジネスロジックはサービス層
    (app/services/uml_diagram_service.py)が担い、ここでは純粋な永続化操作のみを提供する。"""

    model = UmlDiagram

    async def create(
        self, *, project_id: uuid.UUID, view: str, notation: str, semantic_model: dict
    ) -> UmlDiagram:
        """新規UML図をセッションに追加し、flushしてIDを確定させた状態で返す
        (status/versionはORM側のデフォルト値'draft'/1のまま)。"""
        diagram = UmlDiagram(
            project_id=project_id, view=view, notation=notation, semantic_model=semantic_model
        )
        self._session.add(diagram)
        await self._session.flush()
        return diagram

    async def get_by_id(self, diagram_id: uuid.UUID, *, project_id: uuid.UUID) -> UmlDiagram | None:
        """図IDとプロジェクトIDの両方が一致するものだけを取得する
        (ProjectRepository.get_by_idのuser_idスコープと同じ考え方)。"""
        return await self.find_one(id=diagram_id, project_id=project_id)

    async def list_for_project(self, project_id: uuid.UUID) -> list[UmlDiagram]:
        """指定プロジェクトのUML図一覧を更新日時の降順で取得する。"""
        return await self.list_all(order_by=UmlDiagram.updated_at.desc(), project_id=project_id)
