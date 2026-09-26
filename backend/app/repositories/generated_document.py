import uuid

from app.models.generated_document import GeneratedDocument
from app.repositories.base import CRUDRepository

# 生成される4種のドキュメント種別。GeneratedDocument.doc_type の取りうる値と対応する。
DOC_TYPES = ("requirements", "external_design", "internal_design", "implementation_plan")

# 同一project_id+doc_typeにつき保管する最大バージョン数(docs/internal_design.md 3.2節「バージョニング方針」)。
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

        document = GeneratedDocument(
            project_id=project_id, doc_type=doc_type, content=content, version=next_version
        )
        self._session.add(document)
        await self._session.flush()

        # existingは新バージョン追加"前"の一覧(新しい順)。追加後の総数が上限を超える古いものを削除する。
        keep_count = MAX_VERSIONS_PER_DOC_TYPE - 1
        for stale in existing[keep_count:]:
            await self._session.delete(stale)
        if len(existing) > keep_count:
            await self._session.flush()

        return document

    async def get_latest(
        self, *, project_id: uuid.UUID, doc_type: str
    ) -> GeneratedDocument | None:
        """指定project_id・doc_typeの最新バージョンを1件取得する。"""
        rows = await self.list_all(
            order_by=GeneratedDocument.version.desc(), project_id=project_id, doc_type=doc_type
        )
        return rows[0] if rows else None

    async def list_latest_for_project(self, project_id: uuid.UUID) -> list[GeneratedDocument]:
        """4種のドキュメントそれぞれについて、存在する場合は最新バージョンのみを集めて返す。"""
        results = []
        for doc_type in DOC_TYPES:
            latest = await self.get_latest(project_id=project_id, doc_type=doc_type)
            if latest is not None:
                results.append(latest)
        return results
