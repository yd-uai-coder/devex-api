from typing import Annotated

from pydantic import BaseModel, StringConstraints, field_validator

from app.ai.llm.catalog import Provider, Purpose

# 登録する API キー(前後の空白を除く。どの provider のキーもこの長さに収まる)
type ApiKeyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=8, max_length=512)
]


class ProviderRead(BaseModel):
    """AI の提供元と、その表示名。"""

    provider: Provider
    label: str


class CatalogModelRead(BaseModel):
    """選べるモデル1つ。"""

    provider: Provider
    model_id: str
    label: str
    note: str


class PurposeRead(BaseModel):
    """用途の表示名と説明。`recommend_low_cost` は費用の低いモデルを推奨する注記を出す用途。"""

    purpose: Purpose
    label: str
    description: str
    recommend_low_cost: bool


class LlmCatalogRead(BaseModel):
    """GET /llm/catalog の応答(provider・選べるモデル・用途の表)。"""

    providers: list[ProviderRead]
    models: list[CatalogModelRead]
    purposes: list[PurposeRead]


class ModelChoice(BaseModel):
    """用途1つの選択。"""

    purpose: Purpose
    provider: Provider
    model: str


class ModelSettingsUpdate(BaseModel):
    """PUT /users/me/models のリクエストボディ。選択の全体を置き換え、含めない用途は
    既定値に従う。"""

    choices: list[ModelChoice]

    @field_validator("choices")
    @classmethod
    def _unique_purposes(cls, value: list[ModelChoice]) -> list[ModelChoice]:
        purposes = [choice.purpose for choice in value]
        if len(purposes) != len(set(purposes)):
            raise ValueError("同じ用途を2回選んでいます")
        return value


class PurposeSettingRead(BaseModel):
    """用途1つの今の設定。

    `provider`・`model` はユーザーの選択(選んでいなければ既定値)。`is_default` は既定値に従って
    いるか。`key_missing` は、選んだ provider のキーが無く(消した)、使うときは既定値に従うこと。
    `selectable_providers` は、このユーザーがこの用途に選べる provider(既定値と同じ provider・
    自分のキーがある provider。superuser はすべて)。
    """

    purpose: Purpose
    provider: Provider
    model: str
    is_default: bool
    key_missing: bool
    default_provider: Provider
    default_model: str
    selectable_providers: list[Provider]


class ModelSettingsRead(BaseModel):
    """GET・PUT /users/me/models の応答(4用途)。"""

    settings: list[PurposeSettingRead]


class ApiKeyRead(BaseModel):
    """登録済みのキー1つ(キー本体は返さない)。`needs_reregister` は今の鍵で復号できないこと。"""

    provider: Provider
    key_hint: str
    needs_reregister: bool


class ApiKeysRead(BaseModel):
    """GET /users/me/api-keys の応答。`encryption_available` が偽なら、登録は 503 で断られる。"""

    encryption_available: bool
    keys: list[ApiKeyRead]


class ApiKeyUpdate(BaseModel):
    """PUT /users/me/api-keys/{provider} のリクエストボディ。"""

    api_key: ApiKeyText
