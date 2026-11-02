"""資料と合意済みの内容(最新の束)の違いの取り出し(入力・構造化出力の形・検査・やり直し。純粋関数)。

docs/external_design.md 2.17節「違いの一覧」。LLM(生成の用途)に、束の合意の要素の一覧(キー |
名前 | 中身)と資料の本文を渡し、違いを構造化出力で受ける。違い1件は、種類(追加・変更・削除・
確かめる点)・対象(合意の要素のキー)・起点(追加のとき、つながる要件・画面・API)・内容・根拠の
引用。受けた違いは決定的に検査し、通らないものは「弾いた」として理由とともに残す。

- 引用: 引用と本文を同じ規則で正規化(NFKC・空白と改行・md の記号を除く)し、本文の中にあるか。
  短すぎる引用は根拠にならないので弾く。
- 対象・起点: 束の合意の要素のキーにあるか。接頭辞の無い ID(`FR-001`)は、接頭辞を補って
  一つに決まれば、そのキーとみなす。
- 種類と対象の組み合わせ: 変更・削除は対象が要る。追加は対象を持たない(起点は任意。つながる
  合意の要素のキー)。確かめる点(未決・資料どうしの食い違い)は対象が任意で、CR にはしない。起点は
  追加のときだけ持つ。
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agreements.items import AgreedItem, agreement_items
from app.detailed_design.impact.elements import parse_model
from app.documents.external_design.model import ExternalDesignModel
from app.documents.requirements.model import RequirementsModel

DifferenceKind = Literal["add", "change", "delete", "question"]
DIFFERENCE_KIND_LABEL: dict[DifferenceKind, str] = {
    "add": "追加",
    "change": "変更",
    "delete": "削除",
    "question": "確かめる点",
}
# 未確認 → CR にした / 捨てた(理由)。検査で弾いたものは「弾いた」のまま残す
DifferenceStatus = Literal["unconfirmed", "rejected", "converted", "dismissed"]
DIFFERENCE_STATUS_LABEL: dict[DifferenceStatus, str] = {
    "unconfirmed": "未確認",
    "rejected": "弾いた",
    "converted": "CR にした",
    "dismissed": "捨てた",
}
# やり直しで置き換える状態(人が判断していないもの)。CR にした・捨てたは残す
REPLACED_STATUSES: tuple[DifferenceStatus, ...] = ("unconfirmed", "rejected")

# 正規化した後の引用の最短の文字数(これより短いものは根拠にならない)
MIN_QUOTE_CHARS = 6
# 1回の取り出しで受ける違いの数・文の長さの上限(保存の前に切る)
MAX_DIFFERENCES = 50
CONTENT_MAX = 1000
QUOTE_MAX = 1000
KEY_MAX = 300

# 接頭辞の無い対象を補うときに試す接頭辞
_PREFIXES = ("req:", "screen:", "api:", "integration:")

DIFFERENCE_SYSTEM_PROMPT = """\
あなたはソフトウェア開発の変更の受付の担当者です。クライアントと合意済みの内容(要件定義書と
外部設計書の要素の一覧)と、その後に受け取った資料(議事録・メール・資料)を渡します。資料の中で、
合意済みの内容と違うこと(新しい要望・変更・取りやめ)と、確かめる必要があることを、日本語で
挙げてください。

- 種類: add=新しく足すもの(合意済みの内容に無い) / change=合意済みの要素の中身を変えるもの /
  delete=合意済みの要素をやめるもの / question=未決・資料どうしの食い違いで、確かめる必要がある
  もの。
- 合意済みの要素に、合意済みの内容に無い数値・上限・条件などの詳細を決めるもの(例: 「制限する」
  だけの要素に回数を決める)は、言い換えでなく change にする。
- target_key: change と delete は、変わる合意済みの要素のキーを一覧の「キー」から1つ、そのまま
  書く。add は空にする。question は関わる要素があればそのキー、無ければ空。
- origin_key: add のとき、新しいものがつながる合意済みの要素のキーを一覧から1つ(無ければ空)。
  ほかの種類では空にする。
- content: 何がどう違うかを1〜2文で書く(合意済みの内容 → 資料の内容)。
- quote: 根拠になる資料の文を、資料の連続した1か所からそのまま写す(言い換え・要約・「…」での
  省略をしない)。
- 次のものは挙げない: 合意済みの内容と同じことの言い換え・確認、撤回された提案(同じ資料の中で
  元に戻ったもの)、既に済んだこと、日程などの要件でない話、合意済みの内容どうしの食い違い
  (資料と関係ないもの)。
- 結論が出ていない話は、add / change / delete と断定せず question にする。同じ資料に結論の
  出ていない話があっても、決まったこと(依頼・指示)は別の件として挙げる。
- 1つの違いは1件にまとめる。違いが無ければ空の一覧を返す。
"""


class DifferenceItemOut(BaseModel):
    """取り出した違い1件。"""

    kind: DifferenceKind = Field(description="add / change / delete / question")
    target_key: str = Field(default="", description="変わる合意済みの要素のキー(一覧のキー)")
    origin_key: str = Field(default="", description="add のとき、つながる合意済みの要素のキー")
    content: str = Field(description="何がどう違うか(1〜2文)")
    quote: str = Field(description="根拠になる資料の文(資料の連続した1か所をそのまま)")


class DifferenceListOut(BaseModel):
    """違いの取り出しの構造化出力。"""

    differences: list[DifferenceItemOut] = Field(default_factory=list, description="違いの一覧")


@dataclass(frozen=True)
class AgreedListing:
    """束の合意の要素の一覧(LLM に渡す文)と、対象・起点に選べるキー。"""

    text: str
    keys: frozenset[str]


@dataclass(frozen=True)
class MaterialFacts:
    """違いを取り出す資料1件(種類のラベル・題・日付・本文)。"""

    kind_label: str
    title: str
    material_date: date
    body: str


@dataclass(frozen=True)
class CheckedDifference:
    """検査した違い1件。`reasons`が空なら通った(未確認)、あれば弾いた。対象・起点は、接頭辞を
    補って決まったキー(決まらなければ LLM が書いたまま)。"""

    kind: DifferenceKind
    target_key: str
    origin_key: str
    content: str
    quote: str
    reasons: tuple[str, ...] = ()

    @property
    def status(self) -> DifferenceStatus:
        return "rejected" if self.reasons else "unconfirmed"

    @property
    def identity(self) -> tuple[str, str, str]:
        """同じ違いかを見る値(種類・対象・正規化した引用)。やり直しの重なりを除くのに使う。"""
        return (self.kind, self.target_key, normalize_quote(self.quote))


def agreed_listing(snapshots: Mapping[str, Mapping[str, Any] | None]) -> AgreedListing:
    """束の写し(key → model)から、要件定義書・外部設計書の合意の要素の一覧を作る。要素の行は
    「キー | 名前 | 中身」。目的と範囲(1.3)は、キーを持たない参考として添える。"""
    requirements = parse_model(RequirementsModel, snapshots.get("requirements"))
    external_design = parse_model(ExternalDesignModel, snapshots.get("external_design"))
    items = agreement_items(requirements, external_design)
    lines = ["# 合意済みの要素(キー | 名前 | 中身)", ""]
    lines += [f"- {item.key} | {_name(item)} | {_body(item)}" for item in items]
    if requirements is not None:
        lines += ["", "# 参考(キーは無い。対象にしない)", "", f"- 目的: {requirements.purpose}"]
        lines += [f"- 範囲に含む: {s}" for s in requirements.in_scope]
        lines += [f"- 範囲の外: {s}" for s in requirements.out_of_scope]
    return AgreedListing(
        text="\n".join(lines),
        keys=frozenset(item.key for item in items),
    )


def build_difference_messages(listing: AgreedListing, material: MaterialFacts) -> list[BaseMessage]:
    """違いの取り出しの LLM の入力(合意の要素の一覧と、資料1件)。"""
    human = (
        f"{listing.text}\n\n"
        f"# 資料({material.kind_label}「{material.title}」{material.material_date.isoformat()})\n\n"
        f"{material.body}"
    )
    return [SystemMessage(content=DIFFERENCE_SYSTEM_PROMPT), HumanMessage(content=human)]


# 引用の照合で除く文字: 空白・改行と、md の記号(強調・コード・取り消し線・見出し・引用)。LLM が
# 改行を「\n」の2文字で書いた引用もあるので、文字どおりの「\n」「\r」「\t」も改行とみなして除く
_IGNORED = re.compile(r"(?:\\[nrt]|[\s*_~`#>])+")
# 引用の前後に LLM が付けがちな括弧
_QUOTE_MARKS = "「」『』\"'“”"
_ELLIPSIS = re.compile(r"…|‥|\.\.\.")


def normalize_quote(text: str) -> str:
    """引用と本文を照合する正規形(NFKC で全角・半角をそろえ、空白・改行〈文字どおりの「\\n」も〉と
    md の記号を除く)。"""
    return _IGNORED.sub("", unicodedata.normalize("NFKC", text))


def quote_found(quote: str, body: str) -> bool:
    """引用が本文に(正規化した上で)そのままあるか。"""
    needle = normalize_quote(quote.strip().strip(_QUOTE_MARKS))
    return bool(needle) and needle in normalize_quote(body)


def resolve_key(raw: str, keys: Iterable[str]) -> str:
    """LLM が書いた対象を、合意の要素のキーに決める。そのままあればそれ、無ければ接頭辞を補って
    一つに決まるもの。決まらなければ書いたまま(検査で弾く)。"""
    value = raw.strip()
    known = frozenset(keys)
    if not value or value in known:
        return value
    hits = [f"{prefix}{value}" for prefix in _PREFIXES if f"{prefix}{value}" in known]
    return hits[0] if len(hits) == 1 else value


def check_difference(
    item: DifferenceItemOut, listing: AgreedListing, body: str
) -> CheckedDifference:
    """違い1件を検査する(引用・対象と起点のキー・種類と対象の組み合わせ)。文は上限で切る。"""
    target = resolve_key(item.target_key, listing.keys)[:KEY_MAX]
    origin = resolve_key(item.origin_key, listing.keys)[:KEY_MAX] if item.kind == "add" else ""
    quote = item.quote.strip()[:QUOTE_MAX]
    reasons: list[str] = []
    if not item.content.strip():
        reasons.append("内容が空")
    reasons += _quote_problems(quote, body)
    if item.kind in ("change", "delete") and not target:
        reasons.append(f"{DIFFERENCE_KIND_LABEL[item.kind]}に対象が無い")
    if item.kind == "add" and target:
        reasons.append("追加に対象がある(新しいものは起点で示す)")
    elif target and target not in listing.keys:
        reasons.append(f"対象 {target} が束に無い")
    if origin and origin not in listing.keys:
        reasons.append(f"起点 {origin} が束に無い")
    return CheckedDifference(
        kind=item.kind,
        target_key=target,
        origin_key=origin,
        content=item.content.strip()[:CONTENT_MAX],
        quote=quote,
        reasons=tuple(reasons),
    )


def check_differences(
    out: DifferenceListOut, listing: AgreedListing, body: str
) -> list[CheckedDifference]:
    """取り出した違いを検査する(上限の数まで、出てきた順)。"""
    return [check_difference(item, listing, body) for item in out.differences[:MAX_DIFFERENCES]]


def fresh_differences(
    new: Sequence[CheckedDifference], kept: Iterable[CheckedDifference]
) -> list[CheckedDifference]:
    """やり直しで足す違い。残した違い(CR にした・捨てた)と、この回の前の方の違いと、同じ種類・
    対象・引用のものを除く(捨てた違いが毎回戻らないように)。"""
    seen = {k.identity for k in kept}
    out: list[CheckedDifference] = []
    for difference in new:
        if difference.identity in seen:
            continue
        seen.add(difference.identity)
        out.append(difference)
    return out


def _quote_problems(quote: str, body: str) -> list[str]:
    if not quote:
        return ["引用が無い"]
    if quote_found(quote, body):
        if len(normalize_quote(quote.strip(_QUOTE_MARKS))) < MIN_QUOTE_CHARS:
            return ["引用が短すぎる"]
        return []
    if _ELLIPSIS.search(quote):
        return ["引用を省略でつないでいる"]
    return ["引用が資料に無い"]


def _name(item: AgreedItem) -> str:
    content = item.content
    match item.kind:
        case "requirement":
            category = f"・{content['category']}" if content.get("category") else ""
            priority = f"・{content['priority']}" if content.get("priority") else ""
            return f"要件 {content.get('kind', '')}{priority}{category}"
        case "users":
            return item.label
        case "screen":
            return f"画面 {content.get('name', '')}"
        case "api":
            return f"API {content.get('summary', '')}".rstrip()
        case "integration":
            return f"外部連携 {content.get('service', '')}"


def _body(item: AgreedItem) -> str:
    content = item.content
    match item.kind:
        case "requirement":
            return str(content.get("text", ""))
        case "users":
            users = content["users"]
            return " / ".join(f"{u.get('user', '')}: {u.get('scene', '')}" for u in users)
        case "screen":
            return _join(
                ("役割", content.get("role")),
                ("要件", content.get("req_ids")),
                ("UI", content.get("ui_spec")),
            )
        case "api":
            return _join(
                ("画面", content.get("screens")),
                ("要件", content.get("req_ids")),
                ("本文", content.get("body")),
                ("応答", content.get("response")),
                ("誤り", content.get("errors")),
            )
        case "integration":
            return _join(
                ("目的", content.get("purpose")),
                ("認証", content.get("auth")),
                ("タイミング", content.get("timing")),
                ("要件", content.get("req_ids")),
            )


def _join(*fields: tuple[str, Any]) -> str:
    parts = []
    for label, value in fields:
        text = ", ".join(value) if isinstance(value, list) else str(value or "")
        text = " ".join(text.split())
        if text:
            parts.append(f"{label}: {text}")
    return " / ".join(parts)
