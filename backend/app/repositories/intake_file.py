import uuid

from app.models.intake_file import IntakeFile
from app.repositories.base import CRUDRepository


class IntakeFileRepository(CRUDRepository[IntakeFile]):
    """IntakeFileモデルに対する永続化操作をまとめるリポジトリ。"""

    model = IntakeFile

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        filename: str,
        file_type: str,
        size_bytes: int,
        extracted_text: str | None,
        status: str,
        error_message: str | None = None,
    ) -> IntakeFile:
        """添付ファイルのテキスト化結果を1件追加し、flushしてIDを確定させた状態で返す。"""
        entry = IntakeFile(
            project_id=project_id,
            filename=filename,
            file_type=file_type,
            size_bytes=size_bytes,
            extracted_text=extracted_text,
            status=status,
            error_message=error_message,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list_for_project(self, project_id: uuid.UUID) -> list[IntakeFile]:
        """指定プロジェクトの添付ファイル一覧を作成日時の昇順で取得する。"""
        return await self.list_all(order_by=IntakeFile.created_at.asc(), project_id=project_id)
