import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select

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
        mode: str = "simple",
    ) -> Project:
        """新規プロジェクトをセッションに追加し、flushしてIDを確定させた状態で返す。"""
        project = Project(
            user_id=user_id, title=title, intake=intake, template_id=template_id, mode=mode
        )
        self._session.add(project)
        await self._session.flush()
        return project

    async def get_by_id(self, project_id: uuid.UUID, *, user_id: uuid.UUID) -> Project | None:
        """プロジェクトIDと所有者IDの両方が一致するプロジェクトのみを取得する(他ユーザーのプロジェクトは取得できない)。
        削除の印が付いたプロジェクトは取得しない。"""
        return await self.find_one(id=project_id, user_id=user_id, deleted_at=None)

    async def list_for_user(self, user_id: uuid.UUID) -> list[Project]:
        """指定ユーザーのプロジェクト一覧を更新日時の降順で取得する(削除の印が付いたものは除く)。"""
        return await self.list_all(
            order_by=Project.updated_at.desc(), user_id=user_id, deleted_at=None
        )

    async def mark_deleted(self, project: Project) -> None:
        """削除の印を付ける。印だけの更新なので、生成中のジョブが持つ外部キーのロックを待たない。"""
        project.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def is_live(self, project_id: uuid.UUID) -> bool:
        """プロジェクトの行があり、削除の印が無いか。identity map を使わずに DB へ問い合わせる
        (バックグラウンドの処理が、別のリクエストで消された・印が付いたことを見分けるため)。"""
        stmt = select(Project.id).where(Project.id == project_id, Project.deleted_at.is_(None))
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def purge_deleted(self) -> int:
        """削除の印が付いた行を物理削除し、消した数を返す(子・孫の行は DB の CASCADE で消える)。
        ほかのトランザクションがロックしている行(生成中のジョブが子の行を書いた後など)は飛ばす。"""
        stmt = (
            select(Project.id)
            .where(Project.deleted_at.is_not(None))
            .with_for_update(skip_locked=True)
        )
        ids = list((await self._session.execute(stmt)).scalars())
        if ids:
            await self._session.execute(delete(Project).where(Project.id.in_(ids)))
        return len(ids)
