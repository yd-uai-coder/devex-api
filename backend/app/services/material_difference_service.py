import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.catalog import Purpose
from app.materials import (
    REPLACED_STATUSES,
    CheckedDifference,
    DifferenceListOut,
    MaterialFacts,
    agreed_listing,
    build_difference_messages,
    check_differences,
    fresh_differences,
)
from app.models.material import Material
from app.models.material_difference import MaterialDifference, MaterialExtraction
from app.models.project import Project
from app.repositories.baseline import BaselineRepository
from app.repositories.material_difference import MaterialDifferenceRepository
from app.schemas.baseline import BaselineFreshnessRead
from app.services.baseline_service import BaselineService, baseline_snapshots
from app.services.errors import (
    MaterialBaselineMissingError,
    MaterialDifferenceNotFoundError,
    MaterialDifferenceStateError,
)
from app.services.llm_resolver import LlmResolver, ResolvedLlm, injected_llm
from app.services.llm_retry import invoke_structured
from app.services.material_service import MaterialService

logger = structlog.get_logger(__name__)

# 資料の種類のラベル(LLM に渡す資料の見出し)
MATERIAL_KIND_LABEL = {"minutes": "議事録", "email": "メール", "document": "資料"}


@dataclass(frozen=True)
class DifferencesView:
    """資料1件の違いの一覧。`extraction`はまだ取り出していなければ None、`latest_baseline_number`
    は今の最新の束(無ければ None)。"""

    material: Material
    extraction: MaterialExtraction | None
    latest_baseline_number: int | None
    differences: list[MaterialDifference]


class MaterialDifferenceService:
    """資料と最新の束の違いの取り出し・一覧・捨てる / 戻す(docs/external_design.md 2.17節)。

    比べる相手は取り出しの時点の最新の束の要件定義書・外部設計書(合意の要素の一覧)。束が古くても
    止めず、その時点の鮮度を回の記録に残して返す。LLM(生成の用途。要約・案の下書きと同じ)の出力は
    app/materials/differences.py で決定的に検査し、通らないものは「弾いた」で残す。やり直すと、
    未確認と弾いたは置き換わり、CR にした・捨てたは残る(同じ違いは足し直さない)。詳細設計モード
    だけ(資料と同じ)。"""

    def __init__(self, session: AsyncSession, *, llm: Any = None) -> None:
        # llm: テストが差し替える取り出しの LLM(None なら利用者の生成の用途で解決する)
        self._session = session
        self._llm = llm
        self._differences = MaterialDifferenceRepository(session)

    async def extract(
        self, project: Project, material_id: uuid.UUID, *, user_id: uuid.UUID
    ) -> DifferencesView:
        """資料1件と最新の束から違いを取り出す。束が無ければ MaterialBaselineMissingError。LLM が
        失敗したら、前の一覧は変えない。"""
        material = await self._material(project, material_id)
        baseline = await BaselineRepository(self._session).latest(project.id)
        if baseline is None:
            raise MaterialBaselineMissingError(
                "合意済みの版(束)がありません。束を作ってから取り出してください"
            )
        freshness = (await BaselineService(self._session).freshness_of(project, [baseline]))[
            baseline.id
        ]
        listing = agreed_listing(baseline_snapshots(baseline))
        facts = MaterialFacts(
            kind_label=MATERIAL_KIND_LABEL.get(material.kind, material.kind),
            title=material.title,
            material_date=material.material_date,
            body=material.body,
        )
        resolved = await self._generation_llm(user_id)
        out = await invoke_structured(
            resolved.llm, DifferenceListOut, build_difference_messages(listing, facts)
        )
        checked = check_differences(out, listing, material.body)

        await self._differences.delete_with_statuses(material.id, list(REPLACED_STATUSES))
        kept = [_checked(row) for row in await self._differences.list_for(material.id)]
        position = await self._differences.next_position(material.id)
        for offset, difference in enumerate(fresh_differences(checked, kept)):
            await self._differences.add(
                material_id=material.id,
                position=position + offset,
                kind=difference.kind,
                target_key=difference.target_key,
                origin_key=difference.origin_key,
                content=difference.content,
                quote=difference.quote,
                status=difference.status,
                reasons=list(difference.reasons),
                baseline_number=baseline.number,
            )
        await self._differences.replace_extraction(
            material_id=material.id,
            baseline_number=baseline.number,
            freshness=BaselineFreshnessRead.of(freshness).model_dump(mode="json"),
            generated_model=resolved.model,
            generated_by=user_id,
        )
        await self._session.commit()
        logger.info(
            "material_differences_extracted",
            project_id=str(project.id),
            material_id=str(material.id),
            baseline_number=baseline.number,
            fresh=freshness.fresh,
            differences=len(checked),
            model=resolved.model,
        )
        return await self._view(project, material)

    async def list_for(self, project: Project, material_id: uuid.UUID) -> DifferencesView:
        """資料1件の違いの一覧(取り出しの回の記録と、今の最新の束の番号つき)。"""
        return await self._view(project, await self._material(project, material_id))

    async def dismiss(
        self,
        project: Project,
        material_id: uuid.UUID,
        difference_id: uuid.UUID,
        *,
        reason: str,
        actor_id: uuid.UUID | None,
    ) -> MaterialDifference:
        """未確認の違いを、理由をつけて捨てる(確かめる点を閉じるのも同じ)。未確認でなければ
        MaterialDifferenceStateError。"""
        difference = await self._load(project, material_id, difference_id)
        if difference.status != "unconfirmed":
            raise MaterialDifferenceStateError("捨てられるのは未確認の違いだけです")
        difference.status = "dismissed"
        difference.dismiss_reason = reason
        difference.decided_by = actor_id
        difference.decided_at = datetime.now(UTC)
        await self._session.commit()
        return difference

    async def restore(
        self, project: Project, material_id: uuid.UUID, difference_id: uuid.UUID
    ) -> MaterialDifference:
        """捨てた違いを未確認に戻す。捨てたものでなければ MaterialDifferenceStateError。"""
        difference = await self._load(project, material_id, difference_id)
        if difference.status != "dismissed":
            raise MaterialDifferenceStateError("戻せるのは捨てた違いだけです")
        difference.status = "unconfirmed"
        difference.dismiss_reason = None
        difference.decided_by = None
        difference.decided_at = None
        await self._session.commit()
        return difference

    async def _material(self, project: Project, material_id: uuid.UUID) -> Material:
        return (await MaterialService(self._session).get(project, material_id)).material

    async def _load(
        self, project: Project, material_id: uuid.UUID, difference_id: uuid.UUID
    ) -> MaterialDifference:
        material = await self._material(project, material_id)
        difference = await self._differences.get_in_material(
            material_id=material.id, difference_id=difference_id
        )
        if difference is None:
            raise MaterialDifferenceNotFoundError(f"Difference {difference_id} not found")
        return difference

    async def _view(self, project: Project, material: Material) -> DifferencesView:
        numbers = await BaselineRepository(self._session).numbers(project.id)
        return DifferencesView(
            material=material,
            extraction=await self._differences.extraction_for(material.id),
            latest_baseline_number=numbers[-1] if numbers else None,
            differences=await self._differences.list_for(material.id),
        )

    async def _generation_llm(self, user_id: uuid.UUID) -> ResolvedLlm:
        if self._llm is not None:
            return injected_llm(self._llm, Purpose.GENERATION)
        return await LlmResolver(self._session).resolve(user_id, Purpose.GENERATION)


def _checked(row: MaterialDifference) -> CheckedDifference:
    """残した違いの行を、重なりを見る形にする(理由は使わない)。"""
    return CheckedDifference(
        kind=row.kind,  # type: ignore[arg-type]
        target_key=row.target_key,
        origin_key=row.origin_key,
        content=row.content,
        quote=row.quote,
    )
