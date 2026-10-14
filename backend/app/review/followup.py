"""採用した指摘の追跡(採用したのに直していない指摘を見つける。純粋関数)。

安いものから2段で決める(費用なし・決定的)。
- 版(L0): 指摘の関連文書(`documents`)の版・段階の版が、レビューしたとき(回の`targets`)から
  変わっていなければ「未対応」(`unaddressed`)。
- 行(L1): 版は変わったが、指摘の対象の行が変わっていなければ「対象が変わっていない」
  (`target_unchanged`)。対象の行が変わった・行を比べられない(レビューした版が残っていない・
  対象を行に結べない)ときは「変更あり」(`changed`)。

対象と行は文字で結ぶ(指摘の対象は「FR-001」「POST /api/v1/projects」「テーブル users」のように
書き方がまちまちなため)。行の ID が対象の文字に含まれるか、行の名前と対象のどちらかがもう一方を
含めば同じ行とみなす。
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.detailed_design.impact import Element
from app.detailed_design.stages import stage_key
from app.documents.external_design.model import ExternalDesignModel
from app.documents.internal_design.model import InternalDesignModel
from app.documents.requirements.model import RequirementsModel
from app.documents.revision import external_design_rows, internal_design_rows, requirement_rows
from app.review.gates import DETAILED_DESIGN_DOC, DETAILED_PLAN_DOC

FollowupState = Literal["unaddressed", "target_unchanged", "changed"]

# 詳細設計書・実装計画書(詳細)を書き換えるときに変わる段階
_DOCUMENT_STAGES: dict[str, tuple[int, ...]] = {
    DETAILED_DESIGN_DOC: (1, 2, 3, 4, 5, 6),
    DETAILED_PLAN_DOC: (7,),
}
# 名前で結ぶときの最短の長さ(「F」「1」のような短い名前が何にでも当たらないように)
_MIN_NAME = 2


@dataclass(frozen=True)
class Row:
    """比べる行1つ。`id`は行の ID(要件 ID・API・テーブル名・要素のキーの ID 部分など)、`label`は
    画面に出す名前、`body`は比べる内容。"""

    id: str
    label: str
    body: str


@dataclass(frozen=True)
class RowDiff:
    """版の変わった文書・段階の、レビューしたときの行(`before`)と今の行(`after`)。"""

    before: Sequence[Row]
    after: Sequence[Row]


def version_keys(documents: Sequence[str] | None, reviewed: Mapping[str, int]) -> list[str]:
    """指摘の関連文書を、回の版のキー(文書の種類・"stage:N")に読み替える。関連文書が無い
    (列を足す前の指摘・モデルが書かなかった)ときは、回にレビューした版のすべて。"""
    keys: list[str] = []
    for document in documents or ():
        stages = _DOCUMENT_STAGES.get(document)
        candidates = [stage_key(s) for s in stages] if stages else [document]
        keys += [k for k in candidates if k in reviewed and k not in keys]
    return keys or list(reviewed)


def matches(target: str, row: Row) -> bool:
    """対象の文字が行を指しているか。"""
    text = target.strip()
    if not text:
        return False
    label = row.label.strip()
    if _contains(text, row.id) or (len(label) >= _MIN_NAME and _contains(text, label)):
        return True
    return len(text) >= _MIN_NAME and text in label


def _contains(text: str, name: str) -> bool:
    """`name`が`text`の中に、前後を英数字・ハイフンで続けずに現れるか(FR-001 が FR-0010 に
    当たらないように)。"""
    return bool(name) and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) is not None


def followup_state(
    *,
    documents: Sequence[str] | None,
    targets: Sequence[str],
    reviewed: Mapping[str, int],
    current: Mapping[str, int | None],
    diffs: Mapping[str, RowDiff | None],
) -> FollowupState:
    """採用した指摘1件の追跡の状態。`reviewed`は回の版、`current`は今の版(文書は表示中の版、
    段階は今の内容の版。無くなったものは None)、`diffs`は版の変わったキーの行(比べられなければ
    None か、キーが無い)。"""
    changed_keys = [k for k in version_keys(documents, reviewed) if current.get(k) != reviewed[k]]
    if not changed_keys:
        return "unaddressed"
    found = False
    for key in changed_keys:
        diff = diffs.get(key)
        if diff is None:
            return "changed"
        before = {row.id: row for row in diff.before if _targeted(targets, row)}
        after = {row.id: row for row in diff.after if _targeted(targets, row)}
        if before.keys() != after.keys() or any(
            before[i].body != after[i].body for i in before
        ):
            return "changed"
        found = found or bool(before)
    return "target_unchanged" if found else "changed"


def _targeted(targets: Iterable[str], row: Row) -> bool:
    return any(matches(t, row) for t in targets)


def document_rows(rows: Mapping[str, BaseModel]) -> list[Row]:
    """文書の行(app/documents/revision.py の`*_rows`。キーは「要件 FR-001」「API POST /x」の形)。
    ID はキーの種類の後ろ(処理別データフローの「(トリガー)」は除く)。"""
    result: list[Row] = []
    for key, row in rows.items():
        name = key.split(" ", 1)[1] if " " in key else key
        result.append(
            Row(id=name.split("(", 1)[0], label=key, body=row.model_dump_json())
        )
    return result


def element_rows(elements: Iterable[Element], stage: int) -> list[Row]:
    """段階の要素(app/detailed_design/impact)の行。ID はキーの種類の後ろ(`fn:F-01` → F-01)。"""
    return [
        Row(id=e.key.split(":", 1)[-1], label=e.label, body=e.body)
        for e in elements
        if e.stage == stage
    ]


def document_model_rows(doc_type: str, model: Mapping | None) -> list[Row] | None:
    """保存した文書の model の行。行を比べられない文書(実装計画書)・model の無い版・壊れた
    model は None。"""
    if not model:
        return None
    try:
        if doc_type == "requirements":
            return document_rows(requirement_rows(RequirementsModel.model_validate(model)))
        if doc_type == "external_design":
            return document_rows(external_design_rows(ExternalDesignModel.model_validate(model)))
        if doc_type == "internal_design":
            return document_rows(internal_design_rows(InternalDesignModel.model_validate(model)))
    except ValidationError:
        return None
    return None
