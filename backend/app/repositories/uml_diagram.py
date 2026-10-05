import uuid

from app.models.uml_diagram import UmlDiagram
from app.repositories.base import CRUDRepository


class UmlDiagramRepository(CRUDRepository[UmlDiagram]):
    """UmlDiagramモデルに対する永続化操作をまとめるリポジトリ。楽観ロックの版チェックや
    段階の生成による上書きといったビジネスロジックはサービス層
    (app/services/uml_diagram_service.py・design_stage_generation_service.py)が担い、
    ここでは純粋な永続化操作のみを提供する。"""

    model = UmlDiagram

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        view: str,
        notation: str,
        semantic_model: dict,
        subject: str = "",
        scope: dict | None = None,
        generation_status: str = "completed",
    ) -> UmlDiagram:
        """新規UML図をセッションに追加し、flushしてIDを確定させた状態で返す
        (status/versionはORM側のデフォルト値'draft'/1のまま)。"""
        diagram = UmlDiagram(
            project_id=project_id,
            view=view,
            notation=notation,
            semantic_model=semantic_model,
            subject=subject,
            scope=scope,
            generation_status=generation_status,
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

    async def list_by_notation(self, project_id: uuid.UUID, notation: str) -> list[UmlDiagram]:
        """指定プロジェクト・記法のUML図一覧を取得する(DFDの横断検証で使う)。"""
        return await self.list_all(
            order_by=UmlDiagram.updated_at.desc(), project_id=project_id, notation=notation
        )

    async def get_by_subject(
        self, *, project_id: uuid.UUID, notation: str, subject: str
    ) -> UmlDiagram | None:
        """(project_id, notation, subject)で図を1件取得する(再生成時の上書き対象の検索)。"""
        return await self.find_one(project_id=project_id, notation=notation, subject=subject)
