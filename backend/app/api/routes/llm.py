from fastapi import APIRouter

from app.ai.llm.catalog import CATALOG, PROVIDER_LABELS, PURPOSES
from app.api.deps import CurrentUserDep
from app.schemas.llm_settings import (
    CatalogModelRead,
    LlmCatalogRead,
    ProviderRead,
    PurposeRead,
)

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/catalog", response_model=LlmCatalogRead)
async def read_llm_catalog(_current_user: CurrentUserDep) -> LlmCatalogRead:
    """マイページで選べる provider・モデルと、用途の表(静的な表。app/ai/llm/catalog.py)。"""
    return LlmCatalogRead(
        providers=[
            ProviderRead(provider=provider, label=label)
            for provider, label in PROVIDER_LABELS.items()
        ],
        models=[
            CatalogModelRead(
                provider=m.provider, model_id=m.model_id, label=m.label, note=m.note
            )
            for m in CATALOG
        ],
        purposes=[
            PurposeRead(
                purpose=p.purpose,
                label=p.label,
                description=p.description,
                recommend_low_cost=p.recommend_low_cost,
            )
            for p in PURPOSES
        ],
    )
