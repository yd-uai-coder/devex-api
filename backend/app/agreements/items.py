"""合意の対象になる要素(要件・利用者と使う場面・画面・API・外部連携)の導出と、比べる値(純粋関数)。

docs/external_design.md 2.15節。合意の記録は、変更影響分析の要素のキー(`req:FR-001`・
`screen:SCR-001`・`api:GET /api/v1/x`)で持ち、予測・印・比較と同じキーで照合する。変更影響分析の
要素に無い2つは、次のキーにする。

- 利用者と使う場面(1.2): 行に ID が無く、要件とも結ばないので、1.2 全体を1つの要素
  (`users`)として合意する。
- 外部連携(2.4): 要件と結ぶ(`req_ids`)ので行ごとにし、サービス名をキーにする
  (`integration:<サービス名>`)。

合意した中身(`content`)は行の model をそのまま写す(版は巡回で消えうるため、版を指さない)。
比べる値(`body_of`)は、キーになる欄を除いて正規化した JSON で、変更影響分析の要素の`body`と
同じ形にする。
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import BaseModel

from app.detailed_design.api_list import endpoint_key
from app.documents.external_design.model import (
    ApiRow,
    ExternalDesignModel,
    IntegrationRow,
    ScreenRow,
)
from app.documents.requirements.model import RequirementRow, RequirementsModel, UserScene

AgreedKind = Literal["requirement", "users", "screen", "api", "integration"]

# 文書の並び順(要件定義書 → 外部設計書)。一覧はこの順に並べる
AGREED_KINDS: tuple[AgreedKind, ...] = get_args(AgreedKind)
USERS_KEY = "users"
USERS_LABEL = "利用者と使う場面"

# 種類ごとの、キーになる欄(比べる値から除く)
_KEY_FIELDS: dict[AgreedKind, tuple[str, ...]] = {
    "requirement": ("id",),
    "users": (),
    "screen": ("id",),
    "api": ("method", "path"),
    "integration": ("service",),
}


@dataclass(frozen=True)
class AgreedItem:
    """合意の対象の要素1つ。`content`は行の model(`users`は`{"users": [...]}`)。"""

    key: str
    kind: AgreedKind
    label: str
    content: dict[str, Any]


def integration_key(service: str) -> str:
    return f"integration:{service.strip()}"


def agreement_items(
    requirements: RequirementsModel | None, external_design: ExternalDesignModel | None
) -> list[AgreedItem]:
    """要件定義書・外部設計書の model から、合意の対象の要素を文書の並び順に作る(要件 →
    利用者と使う場面 → 画面 → API → 外部連携)。同じキーが2回出たら最初のものを残す(変更影響
    分析の要素と同じ)。利用者が空・サービス名が空の外部連携は要素にしない。"""
    items: dict[str, AgreedItem] = {}

    def add(key: str, kind: AgreedKind, label: str, row: BaseModel | dict[str, Any]) -> None:
        if key not in items:
            content = row if isinstance(row, dict) else row.model_dump(mode="json")
            items[key] = AgreedItem(key, kind, label, content)

    if requirements is not None:
        for row in requirements.rows:
            add(f"req:{row.id}", "requirement", f"{row.id} {row.text}", row)
        if requirements.users:
            users = [user.model_dump(mode="json") for user in requirements.users]
            add(USERS_KEY, "users", USERS_LABEL, {"users": users})
    if external_design is not None:
        for screen in external_design.screens:
            add(f"screen:{screen.id}", "screen", f"{screen.id} {screen.name}", screen)
        for api in external_design.apis:
            add(f"api:{endpoint_key(api.method, api.path)}", "api", api.label, api)
        for integration in external_design.integrations:
            if integration.service.strip():
                add(
                    integration_key(integration.service),
                    "integration",
                    integration.service.strip(),
                    integration,
                )
    return list(items.values())


def body_of(kind: AgreedKind, content: dict[str, Any]) -> str:
    """合意した中身の比べる値。行の model で読み直して既定値をそろえ(保存した形の欠けた欄で
    違いが出ないように)、キーになる欄を除いた正規化した JSON にする。"""
    if kind == "users":
        users = [UserScene.model_validate(u).model_dump(mode="json") for u in content["users"]]
        return _json({"users": users})
    row_type: type[BaseModel] = {
        "requirement": RequirementRow,
        "screen": ScreenRow,
        "api": ApiRow,
        "integration": IntegrationRow,
    }[kind]
    row = row_type.model_validate(content)
    return _json(row.model_dump(mode="json", exclude=set(_KEY_FIELDS[kind])))


def removed_keys(agreed_keys: Iterable[str], items: Iterable[AgreedItem]) -> list[str]:
    """今の合意にあり、新しく合意する要素に無いキー(今の合意の順)。合意済みの版(束)を作るとき、
    束に入らなかった要素を「外れた」と記録するために使う。"""
    present = {item.key for item in items}
    return [key for key in agreed_keys if key not in present]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
