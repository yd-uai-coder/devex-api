"""AI の下書きから外部設計書の model を組み立てる(純粋関数)。

- 画面ID: AI には仮の画面ID を書かせる(前の版と同じ画面なら前の版の ID)。前の版に在る ID だけを
  引き継ぎ、それ以外は次の番号に振り直す(`app.documents.ids`。要件ID と同じ考え方)。API の行の
  関連画面も、同じ対応表で振り直した ID に付け替える。
- API: メソッドを大文字にそろえ、形の違う行(知らないメソッド・`/`で始まらないパス)と、同じ
  API の2行目を捨てる(`app.detailed_design.api_list`が読める形だけを残す)。
- 要件ID: 大文字にそろえて重複を除く。要件定義書に無い ID も捨てずに残し、検証で指摘する。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.detailed_design.api_list import HTTP_METHODS, endpoint_key
from app.documents.external_design.model import (
    SCREEN_PREFIX,
    ApiRow,
    ExternalDesignModel,
    IntegrationRow,
    ScreenRow,
)
from app.documents.ids import carry_over_ids, initial_next_seq
from app.documents.requirements.model import PRIORITIES, Priority, RequirementsModel

# 1つの欄に複数の ID をまとめて書かれたときの区切り
_ID_SEPARATOR = re.compile(r"[\s・、,/]+")
_EMPTY_CELLS = frozenset({"", "—", "-", "なし"})


@dataclass(frozen=True)
class ScreenDraft:
    """AI が下書きした画面1行(ID は仮)。"""

    id: str
    name: str
    role: str = ""
    priority: str = ""
    req_ids: tuple[str, ...] = ()
    ui_spec: str = ""


@dataclass(frozen=True)
class ApiDraft:
    method: str
    path: str
    summary: str = ""
    screens: tuple[str, ...] = ()
    req_ids: tuple[str, ...] = ()
    body: str = ""
    response: str = ""
    errors: str = ""


@dataclass(frozen=True)
class IntegrationDraft:
    service: str
    purpose: str = ""
    auth: str = ""
    timing: str = ""
    req_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExternalDesignDraft:
    """AI の下書き1回分(構造化出力から`drafting.to_draft`で作る)。"""

    overview: str = ""
    transition: str = ""
    data_io: str = ""
    screens: tuple[ScreenDraft, ...] = ()
    apis: tuple[ApiDraft, ...] = ()
    integrations: tuple[IntegrationDraft, ...] = ()


def split_ids(values: Iterable[str]) -> list[str]:
    """ID の並び(1つの欄に「FR-001・FR-002」とまとめたものも分ける)を、大文字・重複なしにする。"""
    seen: list[str] = []
    for value in values:
        for part in _ID_SEPARATOR.split(value.strip().strip("`")):
            part = part.strip().upper()
            if part not in _EMPTY_CELLS and part not in seen:
                seen.append(part)
    return seen


def _priority(value: str) -> Priority:
    return value if value in PRIORITIES else "Must"  # type: ignore[return-value]


def _cell(value: str) -> str:
    value = value.strip()
    return "" if value in _EMPTY_CELLS else value


def _screens(
    drafts: Iterable[ScreenDraft], previous: ExternalDesignModel | None
) -> tuple[list[ScreenRow], dict[str, str], dict[str, int]]:
    """画面の行・仮の ID → 振り直した ID の対応表・次の番号。"""
    kept = [d for d in drafts if d.name.strip()]
    prev_screens = previous.screens if previous else []
    next_seq = initial_next_seq(
        (SCREEN_PREFIX,), [s.id for s in prev_screens], previous.next_seq if previous else {}
    )
    ids, next_seq = carry_over_ids(
        [(SCREEN_PREFIX, d.id) for d in kept],
        {s.id: SCREEN_PREFIX for s in prev_screens},
        next_seq,
    )
    mapping: dict[str, str] = {}
    rows: list[ScreenRow] = []
    for final_id, d in zip(ids, kept, strict=True):
        mapping.setdefault(d.id.strip().upper(), final_id)
        rows.append(
            ScreenRow(
                id=final_id,
                name=d.name.strip(),
                role=d.role.strip(),
                priority=_priority(d.priority),
                req_ids=split_ids(d.req_ids),
                ui_spec=d.ui_spec.strip(),
            )
        )
    return rows, mapping, next_seq


def _apis(drafts: Iterable[ApiDraft], screen_ids: dict[str, str]) -> list[ApiRow]:
    rows: list[ApiRow] = []
    seen: set[str] = set()
    for d in drafts:
        method = d.method.strip().upper()
        path = d.path.strip().strip("`")
        if method not in HTTP_METHODS or not path.startswith("/"):
            continue
        key = endpoint_key(method, path)
        if key in seen:
            continue
        seen.add(key)
        screens = [screen_ids.get(ref, ref) for ref in split_ids(d.screens)]
        rows.append(
            ApiRow(
                method=method,
                path=path,
                summary=d.summary.strip(),
                screens=list(dict.fromkeys(screens)),
                req_ids=split_ids(d.req_ids),
                body=_cell(d.body),
                response=_cell(d.response),
                errors=_cell(d.errors),
            )
        )
    return rows


def build_model(
    draft: ExternalDesignDraft,
    previous: ExternalDesignModel | None,
    requirements: RequirementsModel | None,
) -> ExternalDesignModel:
    """下書きから model を作る。生成した時点の要件定義書の要件ID を写して持つ(検証・付録が読む)。"""
    screens, screen_ids, next_seq = _screens(draft.screens, previous)
    integrations = [
        IntegrationRow(
            service=d.service.strip(),
            purpose=d.purpose.strip(),
            auth=d.auth.strip(),
            timing=d.timing.strip(),
            req_ids=split_ids(d.req_ids),
        )
        for d in draft.integrations
        if d.service.strip()
    ]
    rows = requirements.rows if requirements else []
    return ExternalDesignModel(
        overview=draft.overview.strip(),
        transition=draft.transition.strip(),
        data_io=draft.data_io.strip(),
        screens=screens,
        apis=_apis(draft.apis, screen_ids),
        integrations=integrations,
        known_req_ids=[r.id for r in rows],
        must_fr_ids=[r.id for r in rows if r.kind == "FR" and r.priority == "Must"],
        next_seq=next_seq,
    )
