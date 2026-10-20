import uuid

from sqlalchemy import select

from app.models.baseline import Baseline, BaselineItem
from app.models.generated_document import GeneratedDocument
from app.repositories.base import CRUDRepository

# 生成される4種のドキュメント種別。GeneratedDocument.doc_type の取りうる値と対応する。
DOC_TYPES = ("requirements", "external_design", "internal_design", "implementation_plan")

# モード(projects.mode)ごとに生成する文書。詳細設計モードは要件定義・外部設計だけを生成し、
# 内部設計・実装計画に当たる部分は段階(docs/internal_design.md 3.3節「4. 詳細設計モード」)
# へ引き継ぐ。
# 並びは生成の順序で、doc_generator_service の _DOC_TYPE_INPUTS の前段が必ず先に来る。
# 生成(doc_generator_service)と、生成の後のレビュー(review_service)が使う。
DOC_TYPES_BY_MODE: dict[str, tuple[str, ...]] = {
    "simple": DOC_TYPES,
    "detailed": ("requirements", "external_design"),
}

# テスト仕様書。両モードで、設計書の後に作る(簡易は4文書に続けて、詳細は段階7の承認の後に
# `start_from="test_spec"`で)。設計レビュー・自己診断・段階の入力には入れないので、
# DOC_TYPES_BY_MODE とは別に持つ(doc_generator_service.generation_targets が足す)。
TEST_SPEC = "test_spec"

# 同一project_id+doc_typeにつき保管する最大バージョン数
# (docs/internal_design.md 3.2節「バージョニング方針」)。CR を渡した再生成で作った版
# (`change_request_id`あり)は、何を合意して反映したかをたどれるように消さず、この数にも入れない。
# 合意済みの版(束)が写した版も、同じく消さず数にも入れない(束の写しと元の版を並べて見られるように)。
MAX_VERSIONS_PER_DOC_TYPE = 3


class GeneratedDocumentRepository(CRUDRepository[GeneratedDocument]):
    """GeneratedDocumentモデルに対する永続化操作をまとめるリポジトリ。"""

    model = GeneratedDocument

    async def create_version(
        self,
        *,
        project_id: uuid.UUID,
        doc_type: str,
        content: str,
        model: dict | None = None,
        generated_model: str | None = None,
        change_request_id: uuid.UUID | None = None,
    ) -> GeneratedDocument:
        """新バージョンを追加する(既存行は上書きしない)。`model`は意味モデルを持つ文書だけ。
        `generated_model`は生成したモデル名、`change_request_id`は反映した CR(渡したときだけ)。
        追加後に保管数がMAX_VERSIONS_PER_DOC_TYPEを超える場合は、最も古いバージョンから順に
        削除して上限を維持する。CR で反映した版と、束が写した版は消さず、数にも入れない。"""
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
            model=model,
            generated_model=generated_model,
            change_request_id=change_request_id,
            version=next_version,
            is_current=True,
        )
        self._session.add(document)
        await self._session.flush()

        # existingは新バージョン追加"前"の一覧(新しい順)。CR で反映していない・束が写していない
        # 版だけを数え、追加後の数が上限を超える古いものを削除する。
        kept = await self._baseline_versions(project_id=project_id, doc_type=doc_type)
        plain = [
            row
            for row in existing
            if row.change_request_id is None and row.version not in kept
        ]
        keep_count = MAX_VERSIONS_PER_DOC_TYPE - (1 if change_request_id is None else 0)
        for stale in plain[keep_count:]:
            await self._session.delete(stale)
        if len(plain) > keep_count:
            await self._session.flush()

        return document

    async def _baseline_versions(self, *, project_id: uuid.UUID, doc_type: str) -> set[int]:
        """束が写した版の番号(プロジェクト・文書の種類ごと)。"""
        stmt = (
            select(BaselineItem.source_version)
            .join(Baseline, BaselineItem.baseline_id == Baseline.id)
            .where(
                Baseline.project_id == project_id,
                BaselineItem.key == doc_type,
                BaselineItem.source_version.is_not(None),
            )
        )
        return {v for v in (await self._session.execute(stmt)).scalars().all() if v is not None}

    async def replace_model(
        self, document: GeneratedDocument, *, content: str, model: dict
    ) -> GeneratedDocument:
        """同じ版の model と md を書き換える(版を増やさない)。推測の要件を利用者が確かめた
        「採用」にするときだけ使う(中身は変わらないので、後続の段階を古くしない)。"""
        document.content = content
        document.model = model
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
        """指定project_id・doc_typeについて、保管されている全バージョン(最大MAX_VERSIONS_PER_DOC_TYPE件と、
        CR で反映した版・束が写した版)を新しい順に返す。"""
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
