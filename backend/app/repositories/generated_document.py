import uuid

from app.models.generated_document import GeneratedDocument
from app.repositories.base import CRUDRepository

# 生成される4種のドキュメント種別。GeneratedDocument.doc_type の取りうる値と対応する。
DOC_TYPES = ("requirements", "external_design", "internal_design", "implementation_plan")

# 同一project_id+doc_typeにつき保管する最大バージョン数
# (docs/internal_design.md 3.2節「バージョニング方針」)。
MAX_VERSIONS_PER_DOC_TYPE = 3


class GeneratedDocumentRepository(CRUDRepository[GeneratedDocument]):
    """GeneratedDocumentモデルに対する永続化操作をまとめるリポジトリ。"""

    model = GeneratedDocument

    async def create_version(
        self, *, project_id: uuid.UUID, doc_type: str, content: str
    ) -> GeneratedDocument:
        """新バージョンを追加する(既存行は上書きしない)。追加後に保管数がMAX_VERSIONS_PER_DOC_TYPEを
        超える場合は、最も古いバージョンから順に削除して上限を維持する。"""
        existing = await self.list_all(
            order_by=GeneratedDocument.version.desc(), project_id=project_id, doc_type=doc_type
        )
        next_version = (existing[0].version + 1) if existing else 1

        for row in existing:
            row.is_current = False

        document = GeneratedDocument(
            project_id=project_id,
            doc_type=doc_type,
            content=content,
            version=next_version,
            is_current=True,
        )
        self._session.add(document)
        await self._session.flush()

        # existingは新バージョン追加"前"の一覧(新しい順)。
        # 追加後の総数が上限を超える古いものを削除する。
        keep_count = MAX_VERSIONS_PER_DOC_TYPE - 1
        for stale in existing[keep_count:]:
            await self._session.delete(stale)
        if len(existing) > keep_count:
            await self._session.flush()

        return document

    async def get_latest(self, *, project_id: uuid.UUID, doc_type: str) -> GeneratedDocument | None:
        """指定project_id・doc_typeの最新バージョン(最大version)を1件取得する。"""
        rows = await self.list_all(
            order_by=GeneratedDocument.version.desc(), project_id=project_id, doc_type=doc_type
        )
        return rows[0] if rows else None

    async def get_current(
        self, *, project_id: uuid.UUID, doc_type: str
    ) -> GeneratedDocument | None:
        """指定project_id・doc_typeの現在表示中(is_current=True)のバージョンを1件取得する。
        currentが無い場合(マイグレーション前のデータ等)は最新バージョンにフォールバックする。"""
        current = await self.find_one(project_id=project_id, doc_type=doc_type, is_current=True)
        if current is not None:
            return current
        return await self.get_latest(project_id=project_id, doc_type=doc_type)

    async def list_current_for_project(self, project_id: uuid.UUID) -> list[GeneratedDocument]:
        """4種のドキュメントそれぞれについて、存在する場合は現在表示中のバージョンのみを集めて返す。"""
        results = []
        for doc_type in DOC_TYPES:
            current = await self.get_current(project_id=project_id, doc_type=doc_type)
            if current is not None:
                results.append(current)
        return results

    async def list_versions(
        self, *, project_id: uuid.UUID, doc_type: str
    ) -> list[GeneratedDocument]:
        """指定project_id・doc_typeについて、保管されている全バージョン(最大MAX_VERSIONS_PER_DOC_TYPE件)
        を新しい順に返す。"""
        return await self.list_all(
            order_by=GeneratedDocument.version.desc(), project_id=project_id, doc_type=doc_type
        )

    async def get_version(
        self, *, project_id: uuid.UUID, doc_type: str, version: int
    ) -> GeneratedDocument | None:
        """指定project_id・doc_type・versionの1件を取得する(復元機能向け)。"""
        return await self.find_one(project_id=project_id, doc_type=doc_type, version=version)

    async def set_current(
        self, *, project_id: uuid.UUID, doc_type: str, version: int
    ) -> GeneratedDocument | None:
        """指定バージョンをcurrentにし、同一project_id・doc_typeの他の版のcurrentを外す。
        指定バージョンが存在しない場合は何も変更せずNoneを返す。"""
        rows = await self.list_versions(project_id=project_id, doc_type=doc_type)
        target = next((row for row in rows if row.version == version), None)
        if target is None:
            return None
        for row in rows:
            row.is_current = row is target
        await self._session.flush()
        return target
