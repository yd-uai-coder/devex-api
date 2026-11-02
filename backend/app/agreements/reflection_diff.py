"""CR の反映で文書の要素がどう変わったか(前 → 後。純粋関数)。

docs/external_design.md 2.14節「反映」。反映の直前の版と、反映で作った版の合意の対象の要素
(`agreement_items`)を要素のキーで突き合わせ、比べる値(`body_of`)が違う要素を、欄ごとの前 → 後に
する。LLM は使わない(システムが決定的に比べる)。

- 種類(`change`): 変更 / 追加 / 削除と、範囲の外で LLM が書き換え・落としたのを合意の形へ戻した
  要素(戻した。版の`protection`。前後の文は同じなので欄は持たない)。
- 区分(`scope`): CR の対象(対象の要件と、CR が足した新しい要件)/ 予測(保存した予測の画面・
  API)/ 範囲の外(それ以外。上流の書き換えで連れて変わった要素など)。

版は巡回で消えうるので、反映のときに求めて時系列の`apply`の行に残す(`changes_to_json`)。
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, get_args

from app.agreements.items import USERS_KEY, AgreedItem, AgreedKind, body_of

ReflectionChangeKind = Literal["changed", "added", "removed", "restored"]
ReflectionScope = Literal["target", "predicted", "outside"]

REFLECTION_CHANGE_KINDS: tuple[ReflectionChangeKind, ...] = get_args(ReflectionChangeKind)
REFLECTION_SCOPES: tuple[ReflectionScope, ...] = get_args(ReflectionScope)
REFLECTION_CHANGE_LABEL: dict[ReflectionChangeKind, str] = {
    "changed": "変更",
    "added": "追加",
    "removed": "削除",
    "restored": "合意の形に戻した",
}
REFLECTION_SCOPE_LABEL: dict[ReflectionScope, str] = {
    "target": "CR の対象",
    "predicted": "予測した要素",
    "outside": "範囲の外",
}

# 欄の表示名(種類ごと。ここに無い欄は比べない)。キーになる欄は`body_of`と同じく除く
_FIELD_LABELS: dict[AgreedKind, dict[str, str]] = {
    "requirement": {
        "text": "内容",
        "kind": "種別",
        "priority": "優先度",
        "category": "分類",
        "source": "出どころ",
        "points": "根拠の観点",
    },
    "users": {"users": "利用者と使う場面"},
    "screen": {
        "name": "画面名",
        "role": "役割",
        "priority": "優先度",
        "req_ids": "要件",
        "ui_spec": "画面の仕様",
    },
    "api": {
        "summary": "概要",
        "screens": "画面",
        "req_ids": "要件",
        "body": "リクエスト",
        "response": "レスポンス",
        "errors": "エラー",
    },
    "integration": {
        "purpose": "目的",
        "auth": "認証",
        "timing": "タイミング",
        "req_ids": "要件",
    },
}


@dataclass(frozen=True)
class FieldChange:
    """欄1つの前 → 後(表示の文。無い側は空)。"""

    name: str
    label: str
    before: str
    after: str


@dataclass(frozen=True)
class ReflectionChange:
    """要素1つの前 → 後。`fields`は違う欄(追加・削除は空でない欄すべて、戻したは空)。
    `restore_reason`は戻したときの理由(`changed` / `dropped`)。"""

    key: str
    kind: AgreedKind
    label: str
    change: ReflectionChangeKind
    scope: ReflectionScope
    fields: tuple[FieldChange, ...] = ()
    restore_reason: str | None = None


def reflection_changes(
    before: Sequence[AgreedItem],
    after: Sequence[AgreedItem],
    *,
    target_keys: Collection[str],
    rewritable: Collection[str],
    adds_requirement: bool,
    protection: Sequence[Mapping[str, Any]] = (),
) -> list[ReflectionChange]:
    """反映の前の要素`before`と後の要素`after`の前 → 後(後の版の並び順、削除は前の版の順で後ろ、
    戻したはその後ろ)。`target_keys`は対象の要件のキー、`rewritable`は書き換えてよい要素のキー
    (対象の要件と予測の画面・API)、`adds_requirement`は CR が新しい要件を足すか、`protection`は
    作った版の戻した要素(`[{key, reason}]`)。"""
    old = {item.key: item for item in before}
    new_keys = {item.key for item in after}

    def scope(key: str, kind: AgreedKind, added: bool) -> ReflectionScope:
        if key in target_keys or (added and adds_requirement and kind == "requirement"):
            return "target"
        if key in rewritable:
            return "predicted"
        return "outside"

    changes: list[ReflectionChange] = []
    for item in after:
        previous = old.get(item.key)
        if previous is None:
            fields = _fields(item.kind, None, item.content)
            changes.append(
                ReflectionChange(
                    item.key,
                    item.kind,
                    item.label,
                    "added",
                    scope(item.key, item.kind, True),
                    fields,
                )
            )
        elif body_of(item.kind, previous.content) != body_of(item.kind, item.content):
            fields = _fields(item.kind, previous.content, item.content)
            changes.append(
                ReflectionChange(
                    item.key,
                    item.kind,
                    item.label,
                    "changed",
                    scope(item.key, item.kind, False),
                    fields,
                )
            )
    for item in before:
        if item.key not in new_keys:
            fields = _fields(item.kind, item.content, None)
            changes.append(
                ReflectionChange(
                    item.key,
                    item.kind,
                    item.label,
                    "removed",
                    scope(item.key, item.kind, False),
                    fields,
                )
            )
    labels = {item.key: item for item in [*before, *after]}
    for entry in protection:
        key = str(entry.get("key", ""))
        known = labels.get(key)
        if known is None:
            continue
        changes.append(
            ReflectionChange(
                key,
                known.kind,
                known.label,
                "restored",
                "outside",
                restore_reason=str(entry.get("reason", "")) or None,
            )
        )
    return changes


def changes_to_json(changes: Sequence[ReflectionChange]) -> list[dict[str, Any]]:
    """時系列の`apply`の行の`refs`に残す形。"""
    return [
        {
            "key": c.key,
            "kind": c.kind,
            "label": c.label,
            "change": c.change,
            "scope": c.scope,
            "fields": [
                {"name": f.name, "label": f.label, "before": f.before, "after": f.after}
                for f in c.fields
            ],
            "restore_reason": c.restore_reason,
        }
        for c in changes
    ]


def changes_from_json(value: Sequence[Mapping[str, Any]]) -> list[ReflectionChange]:
    """残した形を読む。形の正しくない項目は捨てる(詳細の表示を壊さないため)。"""
    changes: list[ReflectionChange] = []
    for item in value:
        try:
            change = item["change"]
            scope = item["scope"]
            if change not in REFLECTION_CHANGE_KINDS or scope not in REFLECTION_SCOPES:
                continue
            fields = tuple(
                FieldChange(str(f["name"]), str(f["label"]), str(f["before"]), str(f["after"]))
                for f in item.get("fields") or []
            )
            changes.append(
                ReflectionChange(
                    key=str(item["key"]),
                    kind=item["kind"],
                    label=str(item.get("label", "")),
                    change=change,
                    scope=scope,
                    fields=fields,
                    restore_reason=item.get("restore_reason"),
                )
            )
        except (KeyError, TypeError):
            continue
    return changes


@dataclass(frozen=True)
class AppliedReflection:
    """文書への反映1回(時系列の`apply`の行1つ)。`documents`は作った版(文書の種類, 版)、
    `changes`は前 → 後(記録の無い、前 → 後を残す前の反映は None)。"""

    occurred_at: datetime
    documents: tuple[tuple[str, int], ...]
    changes: tuple[ReflectionChange, ...] | None


def applied_reflections(
    events: Iterable[tuple[str, datetime, Mapping[str, Any] | None]],
) -> list[AppliedReflection]:
    """時系列の行(操作, 日時, refs)から、文書への反映を新しい順に取り出す(段階への反映は
    `documents`を持たないので入れない)。"""
    reflections = []
    for action, occurred_at, refs in events:
        if action != "apply" or not refs or not refs.get("documents"):
            continue
        documents = tuple(
            (str(d["doc_type"]), int(d["version"]))
            for d in refs["documents"]
            if isinstance(d, dict) and "doc_type" in d and "version" in d
        )
        raw = refs.get("changes")
        changes = tuple(changes_from_json(raw)) if isinstance(raw, list) else None
        reflections.append(AppliedReflection(occurred_at, documents, changes))
    reflections.reverse()
    return reflections


def _fields(
    kind: AgreedKind, before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> tuple[FieldChange, ...]:
    """違う欄の前 → 後(片側が無ければ、もう片側の空でない欄)。"""
    result = []
    for name, label in _FIELD_LABELS[kind].items():
        old = _display(kind, name, before)
        new = _display(kind, name, after)
        if old != new:
            result.append(FieldChange(name, label, old, new))
    return tuple(result)


def _display(kind: AgreedKind, name: str, content: Mapping[str, Any] | None) -> str:
    """欄の表示の文。並び(要件 ID など)は「、」で、利用者と使う場面は1行1人で並べる。"""
    if content is None:
        return ""
    value = content.get(name)
    if kind == "users" and name == USERS_KEY:
        return "\n".join(
            f"{u.get('user', '')}: {u.get('scene', '')}" for u in value or [] if isinstance(u, dict)
        )
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(v) for v in value)
    return str(value)
