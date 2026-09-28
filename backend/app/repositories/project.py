import uuid

from app.models.project import Project
from app.repositories.base import CRUDRepository


class ProjectRepository(CRUDRepository[Project]):
    """Projectモデルに対する永続化操作をまとめるリポジトリ。"""

    model = Project

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        title: str,
        intake: dict | None = None,
        template_id: uuid.UUID | None = None,
    ) -> Project:
        """新規プロジェクトをセッションに追加し、flushしてIDを確定させた状態で返す。"""
        project = Project(user_id=user_id, title=title, intake=intake, template_id=template_id)
        self._session.add(project)
        await self._session.flush()
        return project

    async def get_by_id(self, project_id: uuid.UUID, *, user_id: uuid.UUID) -> Project | None:
        """プロジェクトIDと所有者IDの両方が一致するプロジェクトのみを取得する(他ユーザーのプロジェクトは取得できない)。"""
        return await self.find_one(id=project_id, user_id=user_id)

    async def list_for_user(self, user_id: uuid.UUID) -> list[Project]:
        """指定ユーザーのプロジェクト一覧を更新日時の降順で取得する。"""
        return await self.list_all(order_by=Project.updated_at.desc(), user_id=user_id)
