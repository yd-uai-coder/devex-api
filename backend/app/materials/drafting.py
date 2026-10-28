"""資料から取り出した違いを、変更要求(CR)の下書きにまとめる(純粋関数)。

docs/external_design.md 2.17節「CR の下書き」。開発者が1つの資料の違い(未確認の追加・変更・
削除)を1件以上選ぶと、決定的に下書きを組み立てる。下書きは今までの CR と同じく、編集で直して
説明済みにする。

- 対象: 要件の変更 → その要件の内容の変更、要件の削除 → その要件の削除。画面・API・外部連携の変更・
  削除 → つながる要件(行の`req_ids`のうち束にあるもの。候補)の内容の変更(要素を消しても要件は
  消えない)。開発者が候補から選べば選んだものだけ、選ばなければ候補をすべて対象にする。候補の無い
  要素(利用者と使う場面・要件を持たない画面など)の変更・削除は、新しい要件を足す CR にし、要素を
  起点にする(CR の起点に選べる種類のときだけ)。
- 起点: 追加の起点が要件・画面・API ならそのまま、外部連携ならつながる要件、利用者と使う場面なら
  起点なし(開発者が編集で足す)。
- 題・内容・背景: 題は1件ならその違いの内容、複数なら「<資料の題>」の変更(n件)。内容は違いごとの
  種類・対象・内容・引用の箇条書き。背景は資料の種類(議事録 → 打ち合わせ)・日付・題。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.agreements.items import agreement_items
from app.change_requests.states import BackgroundSource
from app.change_requests.targets import Target, normalize_targets
from app.detailed_design.impact.elements import parse_model
from app.documents.external_design.model import ExternalDesignModel
from app.documents.requirements.model import RequirementsModel
from app.materials.differences import DIFFERENCE_KIND_LABEL, DifferenceKind

# CR にできる違いの種類(確かめる点は CR にしない)
DRAFTABLE_KINDS: tuple[DifferenceKind, ...] = ("add", "change", "delete")
# 資料の種類 → CR の背景の出どころ
BACKGROUND_SOURCE_OF_MATERIAL: dict[str, BackgroundSource] = {
    "minutes": "meeting",
    "email": "email",
    "document": "document",
}
# CR の題・内容の上限(app/schemas/change_request.py の TITLE_MAX・CONTENT_MAX と同じ)
TITLE_MAX = 200
CONTENT_MAX = 20000
# 内容が上限を超えたときに、引用を切り詰める長さ
SHORT_QUOTE_MAX = 200
# CR の起点に選べる合意の要素の接頭辞(要件・画面・API。処理は合意の要素に無い)
_ORIGIN_PREFIXES = ("req:", "screen:", "api:")


@dataclass(frozen=True)
class DraftDifference:
    """下書きにする違い1件(種類・対象・起点・内容・引用)。"""

    kind: DifferenceKind
    target_key: str
    origin_key: str
    content: str
    quote: str


@dataclass(frozen=True)
class DraftMaterial:
    """違いを取り出した資料(種類〈minutes / email / document〉・題・日付)。"""

    kind: str
    title: str
    material_date: date


@dataclass(frozen=True)
class ChangeRequestDraft:
    """組み立てた CR の下書き。`origins`は新しい要件の予測の起点(要素のキー。選んだ順)。"""

    title: str
    content: str
    background_source: BackgroundSource
    background_date: date
    background_memo: str
    targets: tuple[Target, ...]
    adds_requirement: bool
    origins: tuple[str, ...]


def requirement_links(
    snapshots: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, tuple[str, ...]]:
    """束の写し(key → model)から、合意の要素のキー → つながる要件 ID(束の要件定義書にあるもの。
    並びは行のまま・重複なし)。要件はその ID、画面・API・外部連携は行の`req_ids`、利用者と使う場面は
    空。"""
    requirements = parse_model(RequirementsModel, snapshots.get("requirements"))
    external_design = parse_model(ExternalDesignModel, snapshots.get("external_design"))
    items = agreement_items(requirements, external_design)
    known = {item.key.removeprefix("req:") for item in items if item.kind == "requirement"}
    links: dict[str, tuple[str, ...]] = {}
    for item in items:
        if item.kind == "requirement":
            links[item.key] = (item.key.removeprefix("req:"),)
        elif item.kind == "users":
            links[item.key] = ()
        else:
            ids = [str(i).strip() for i in item.content.get("req_ids") or []]
            links[item.key] = tuple(dict.fromkeys(i for i in ids if i in known))
    return links


def requirement_candidates(
    kind: str, target_key: str, links: Mapping[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """違い1件の対象の候補の要件 ID(変更・削除の対象につながる要件。追加・確かめる点は空)。"""
    if kind not in ("change", "delete"):
        return ()
    return links.get(target_key, ())


def origin_keys(origin_key: str, links: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    """追加の起点を CR の起点(要件・画面・API の要素のキー)に写す。外部連携はつながる要件、
    利用者と使う場面・束に無いキーは起点なし。"""
    if not origin_key:
        return ()
    if origin_key.startswith(_ORIGIN_PREFIXES):
        return (origin_key,) if origin_key in links else ()
    if origin_key.startswith("integration:"):
        return tuple(f"req:{req_id}" for req_id in links.get(origin_key, ()))
    return ()


def draft_change_request(
    differences: Sequence[DraftDifference],
    material: DraftMaterial,
    links: Mapping[str, tuple[str, ...]],
    chosen_requirement_ids: Sequence[str] | None = None,
) -> ChangeRequestDraft:
    """選んだ違い(1件以上・追加・変更・削除だけ)から CR の下書きを組み立てる。
    `chosen_requirement_ids`は画面・API・外部連携の違いの対象に選んだ要件(None なら候補すべて)。
    次のときは ValueError: 違いが無い・CR にできない種類がある・選んだ要件が候補に無い・同じ要件が
    内容の変更と削除の両方になる。"""
    if not differences:
        raise ValueError("CR にする違いを1件以上選んでください。")
    unsupported = [d for d in differences if d.kind not in DRAFTABLE_KINDS]
    if unsupported:
        raise ValueError("確かめる点は CR にできません。")
    element_candidates = [
        req_id
        for d in differences
        if not d.target_key.startswith("req:")
        for req_id in requirement_candidates(d.kind, d.target_key, links)
    ]
    chosen = None
    if chosen_requirement_ids is not None:
        chosen = list(dict.fromkeys(i.strip() for i in chosen_requirement_ids if i.strip()))
        unknown = [i for i in chosen if i not in element_candidates]
        if unknown:
            raise ValueError(f"違いの対象につながらない要件です: {', '.join(unknown)}")

    targets: list[Target] = []
    origins: list[str] = []
    adds_requirement = False
    for difference in differences:
        if difference.kind == "add":
            adds_requirement = True
            origins += origin_keys(difference.origin_key, links)
            continue
        if difference.target_key.startswith("req:"):
            req_id = difference.target_key.removeprefix("req:")
            kind = "delete" if difference.kind == "delete" else "content"
            targets.append(Target(req_id, kind))
            continue
        candidates = requirement_candidates(difference.kind, difference.target_key, links)
        picked = [i for i in candidates if chosen is None or i in chosen]
        if picked:
            targets += [Target(req_id) for req_id in picked]
        elif not candidates:
            # つながる要件の無い要素の変更は、要素を起点にした新しい要件として扱う
            adds_requirement = True
            origins += origin_keys(difference.target_key, links)
    return ChangeRequestDraft(
        title=_title(differences, material),
        content=_content(differences),
        background_source=BACKGROUND_SOURCE_OF_MATERIAL.get(material.kind, "other"),
        background_date=material.material_date,
        background_memo=f"資料「{material.title}」({material.material_date.isoformat()})の違い"
        f"{len(differences)}件から作った下書き",
        targets=tuple(normalize_targets(targets)),
        adds_requirement=adds_requirement,
        origins=tuple(dict.fromkeys(origins)),
    )


def _title(differences: Sequence[DraftDifference], material: DraftMaterial) -> str:
    if len(differences) == 1:
        text = " ".join(differences[0].content.split())
    else:
        text = f"「{material.title}」の変更({len(differences)}件)"
    return _cut(text, TITLE_MAX)


def _content(differences: Sequence[DraftDifference]) -> str:
    """違いごとの箇条書き。上限を超えたら引用を切り詰め、それでも超えたら末尾を切る。"""
    full = _lines(differences, quote_max=None)
    if len(full) <= CONTENT_MAX:
        return full
    return _cut(_lines(differences, quote_max=SHORT_QUOTE_MAX), CONTENT_MAX)


def _lines(differences: Sequence[DraftDifference], *, quote_max: int | None) -> str:
    lines = ["資料から取り込んだ違い:", ""]
    for difference in differences:
        place = difference.target_key or (
            f"新規(起点 {difference.origin_key})" if difference.origin_key else "新規"
        )
        quote = " ".join(difference.quote.split())
        if quote_max is not None:
            quote = _cut(quote, quote_max)
        lines.append(
            f"- {DIFFERENCE_KIND_LABEL[difference.kind]} {place}: "
            f"{' '.join(difference.content.split())}"
        )
        lines.append(f"  - 根拠: 「{quote}」")
    return "\n".join(lines)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
