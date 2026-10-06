"""段階1 機能(処理)一覧の意味モデルと、下書きの組み立て(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」。

`design_stages.model`(段階1)の正本の形を`FunctionListModel`で固定する。列は詳細設計書の
01章(処理ID/名称/種別/トリガー/関連画面/機能グループ/概要)と同じ。

処理IDは段階1で振り、再生成しても変えない(後の段階は処理IDで互いを参照するため)。AIの下書きには
IDを書かせず、ここで前の版の行とトリガーで突き合わせて決める:

- トリガーが一致した行は、前の版のIDと、人が確定した機能グループを引き継ぐ。
- 新しい行には`next_number`から番号を振る。消えた行の番号は再利用しない(消えた処理を指していた
  後の段階の参照が、別の処理を指してしまわないため)。

機能グループの初期値は、APIのパスのリソース名から決定的に作る(`initial_group`)。AIには決め
させない(同じ入力から毎回同じ初期値になり、人が確定するときの基準がぶれないため)。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.detailed_design.api_list import parse_trigger, trigger_key

FunctionKind = Literal["API", "API+バッチ", "バッチ", "画面", "その他"]

FUNCTION_ID_PATTERN = re.compile(r"^F-(\d{2,})$")

# パスの先頭の`/api/v1`(版の番号は問わない)
_API_PREFIX = re.compile(r"^/api/v\d+(?=/|$)")


class FunctionRow(BaseModel):
    """機能一覧の1行(処理1つ)。`group_initial`はAPIのパスから作った初期値で、`group`が人の確定値。"""

    id: str
    name: str
    kind: FunctionKind = "API"
    trigger: str = ""
    screens: list[str] = Field(default_factory=list)
    group_initial: str = ""
    group: str = ""
    summary: str = ""


class FunctionListModel(BaseModel):
    """段階1の意味モデル。`groups`は機能グループの並び(表示順)、`next_number`は次に振る番号。"""

    groups: list[str] = Field(default_factory=list)
    functions: list[FunctionRow] = Field(default_factory=list)
    next_number: int = 1


@dataclass(frozen=True)
class FunctionDraft:
    """AIの下書きの1行(IDと機能グループの初期値は持たない)。`group_hint`はAPIでない処理の
    機能グループの提案(APIはパスから決めるので使わない)。"""

    name: str
    kind: FunctionKind
    trigger: str
    screens: tuple[str, ...]
    summary: str
    group_hint: str = ""


def format_function_id(number: int) -> str:
    return f"F-{number:02d}"


def function_number(function_id: str) -> int | None:
    match = FUNCTION_ID_PATTERN.match(function_id)
    return int(match.group(1)) if match is not None else None


def initial_group(trigger: str, *, fallback: str = "その他") -> str:
    """機能グループの初期値。APIのパスの先頭のリソース名で、`/api/v1/<親>/{id}/<子>`のように
    親の個別の対象に属するパスは子のリソース名にする(`/api/v1/projects/{id}/documents` →
    `documents`)。APIでないトリガーは`fallback`(AIの提案)を使う。"""
    parsed = parse_trigger(trigger)
    if parsed is None:
        return fallback.strip() or "その他"
    path = _API_PREFIX.sub("", parsed[1])
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return fallback.strip() or "その他"
    if len(segments) >= 3 and segments[1].startswith("{"):
        return segments[2]
    return segments[0]


def _match_key(trigger: str, name: str) -> str:
    """前の版の行と突き合わせるキー。APIはメソッド+正規化したパス、それ以外は名称。"""
    return trigger_key(trigger) or f"name:{name.strip()}"


def merge_draft(
    drafts: Sequence[FunctionDraft], previous: FunctionListModel | None = None
) -> FunctionListModel:
    """AIの下書きを、前の版(あれば)と突き合わせて機能一覧にする。

    - 前の版とキーが一致した行: IDと機能グループ(人の確定値)を引き継ぐ。
    - 新しい行: `next_number`から番号を振り、機能グループは初期値にする。
    - `groups`: 前の版の並びのうち使われているものを先に、新しいグループを出てきた順に後ろへ。
    - 同じキーの下書きが2行あれば、2行目以降は新しい行として扱う(引き継げるIDは1つだけ)。
    """
    previous = previous or FunctionListModel()
    unclaimed: dict[str, FunctionRow] = {}
    for row in previous.functions:
        unclaimed.setdefault(_match_key(row.trigger, row.name), row)
    next_number = max(
        [previous.next_number]
        + [(function_number(row.id) or 0) + 1 for row in previous.functions]
    )

    functions: list[FunctionRow] = []
    for draft in drafts:
        group_initial = initial_group(draft.trigger, fallback=draft.group_hint)
        matched = unclaimed.pop(_match_key(draft.trigger, draft.name), None)
        if matched is not None:
            function_id, group = matched.id, matched.group or group_initial
        else:
            function_id, group = format_function_id(next_number), group_initial
            next_number += 1
        functions.append(
            FunctionRow(
                id=function_id,
                name=draft.name.strip(),
                kind=draft.kind,
                trigger=draft.trigger.strip(),
                screens=list(draft.screens),
                group_initial=group_initial,
                group=group,
                summary=draft.summary.strip(),
            )
        )

    used = [row.group for row in functions]
    groups = [g for g in previous.groups if g in used]
    groups += [g for g in dict.fromkeys(used) if g not in groups]
    return FunctionListModel(groups=groups, functions=functions, next_number=next_number)
