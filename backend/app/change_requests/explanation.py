"""変更要求(CR)の説明書(5節の md)の組み立てと、任意の要約の照合(純粋関数)。

docs/external_design.md 2.14節「変更の説明書」・2.16節「説明とすり合わせ」。合意の前にクライアントへ
見せる md を、CR の値・基準の合意済みの版(束)の写し・影響の予測から決定的に組み立てる。LLM は
使わず、同じ入力からはいつも同じ md になる。

1. 変更の内容と背景
2. 対象の要件(合意済みの文と変更後の案。新しい要件を足すなら、その予測の起点)
3. 変更の規模(作業単位・ファイル・テスト・画面・API の数。数えられないときは理由。scale.py)
4. 影響の一覧(置き場ごと・理由・届いた対象と新しい要件の起点)
5. 合意の前に確かめたい点(対象の要件の根拠の観点のうち未決のもの・推測の要件を問いの形で)

- 読み手はクライアント。各節の頭に、その節が何かを言う決まった1文を置く。要素は名前を先に書き
  (`トップページ(SCR-001)`)、要件の文は合意の対象なので変えない。
- 5節は、今の観点の表でなく束の要件定義書の写し(`point_statuses`・`rows[].source`)から読む
  (合意した版と同じものを示し、後でヒアリングが進んでも説明書が変わらないように)。
- `fingerprint`は説明書の md の sha256。要約を作った・確かめた時点の値と比べ、違えば要約は「古い」。
- 要約の照合は、要約に出る要件 ID と数字のうち、説明書(見出しを除く)に出ないものを返す。
"""

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.change_requests.prediction import PredictedItem, Prediction, origin_name
from app.change_requests.scale import (
    Scale,
    ScaleCount,
    ScaleRemark,
    display_name,
    predicted_scale,
)
from app.change_requests.states import (
    BACKGROUND_SOURCE_LABEL,
    STATUS_LABEL,
    BackgroundSource,
    ChangeRequestStatus,
)
from app.change_requests.targets import TARGET_KIND_LABEL, Target
from app.hearing.points import is_point_key, point_def

# 置き場(0 = 文書、1〜8 = 詳細設計の段階)の見出し
PLACE_LABEL: dict[int, str] = {
    0: "文書(要件・画面・API)",
    1: "段階1 機能(処理)一覧",
    2: "段階2 データフロー",
    3: "段階3 データモデル",
    4: "段階4 ソフトウェア構造",
    5: "段階5 主要処理の手順",
    6: "段階6 処理ロジック",
    7: "段階7 実装計画",
    8: "段階8 実装手順書",
}
# 束に入っていない文書・段階の、束を作ったときの状態の表示名
EXCLUDED_STATE_LABEL: dict[str, str] = {
    "absent": "無い",
    "not_started": "未着手",
    "draft": "下書き",
    "regenerated": "再生成済・未承認",
    "reviewing": "レビュー中",
    "outdated": "古い",
}
# 束の写しの key の表示名(文書)
DOCUMENT_LABEL: dict[str, str] = {
    "requirements": "要件定義書",
    "external_design": "外部設計書",
    "test_spec": "テスト仕様書",
}
REQUIREMENTS_KEY = "requirements"
DELETED_PROPOSAL = "(削除)"
NO_PROPOSAL = "(1節の内容を参照)"
SUMMARY_HEADING = "## 要約"
NO_COUNT = "数えられない"

# 説明書の頭と各節の頭に置く、読み手向けの1文
READER_INTRO = (
    "この文書は、合意済みの要件と設計を変える依頼について、何を変え、どこまで手が入るかを"
    "説明するものです。合意・却下の前にお読みください。"
)
BASELINE_NOTE = "前回合意した要件と設計の一式"
SECTION_LEAD: dict[str, str] = {
    "request": "この節は、何を・なぜ変えるかです。",
    "targets": (
        "この節は、合意済みの要件のうち変わるものです。「合意済みの文」が今の合意、"
        "「変更後の案」がこの変更で合意したい文です。"
    ),
    "scale": (
        "この節は、変更で手を入れる量の目安です。合意済みの版の設計から決まった規則で"
        "数えたもので、工数や費用ではありません。"
    ),
    "impact": "この節は、合意済みの版の設計のうち、見直しが要る部分です。",
    "questions": "この節は、合意の前にお答えいただきたい点です。",
}

_REQUIREMENT_ID = re.compile(r"\b(?:FR|NFR|CON)-\d+\b")
# 数字(英字に続く・英字が続くものは名前の一部とみなして数えない。`E2E`・`S3`など)
_NUMBER = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?(?![A-Za-z\d])")


@dataclass(frozen=True)
class ChangeRequestFacts:
    """説明書の1節・2節に載る CR の値(DB の行から写したもの)。`origin_labels`は新しい要件の
    予測の起点の表示名(選んだ順)。"""

    number: int
    title: str
    status: ChangeRequestStatus
    content: str
    background_source: BackgroundSource
    background_date: date | None
    background_memo: str
    targets: tuple[Target, ...]
    adds_requirement: bool
    baseline_number: int
    origin_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Explanation:
    """説明書の5節。`header`は題・読み手向けの1文・基準の行、`body`は1〜5節、`scale`は3節の
    規模。"""

    header: str
    body: str
    scale: Scale

    @property
    def markdown(self) -> str:
        return f"{self.header}\n\n{self.body}"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.markdown.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SummaryCheck:
    """要約の照合の結果。説明書に出ない要件 ID と数字(出た順・重なりなし)。"""

    unknown_requirement_ids: tuple[str, ...]
    unknown_numbers: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.unknown_requirement_ids and not self.unknown_numbers


def build_explanation(
    facts: ChangeRequestFacts,
    prediction: Prediction,
    snapshots: Mapping[str, Mapping[str, Any] | None],
    excluded: Sequence[tuple[str, str]],
    stage_states: Mapping[int, str] | None = None,
) -> Explanation:
    """CR の値・予測・束の写し(key → model。入っているものだけ)・束に入っていない文書と段階
    (key, 状態)・束に入っている段階の束を作った時点の状態(段階 → 状態)から、説明書の5節を
    組み立てる。"""
    requirements = snapshots.get(REQUIREMENTS_KEY) or {}
    rows = {str(row["id"]): row for row in requirements.get("rows", []) if row.get("id")}
    scale = predicted_scale(facts.targets, prediction, snapshots, stage_states or {})
    label = f"CR-{facts.number}"
    header = "\n".join(
        [
            f"# 変更の説明書 {label}: {_line(facts.title)}",
            "",
            READER_INTRO,
            "",
            f"- 基準の合意済みの版: B-{facts.baseline_number}({BASELINE_NOTE})",
            f"- CR の状態: {STATUS_LABEL[facts.status]}",
        ]
    )
    sections = [
        _section_request(facts),
        _section_targets(facts, rows),
        _section_scale(scale),
        _section_impact(prediction, excluded),
        _section_questions(facts.targets, rows, requirements.get("point_statuses", {})),
    ]
    return Explanation(header=header, body="\n\n".join(sections), scale=scale)


def explanation_filename(number: int) -> str:
    """ダウンロードのファイル名(文書の`{種類}.md`と同じく ASCII)。"""
    return f"cr-{number}-explanation.md"


def with_summary(explanation: Explanation, summary: str | None) -> str:
    """出力する md。確かめた要約があれば、題の後ろ・1節の前に「要約」の節を置く。"""
    if not summary or not summary.strip():
        return explanation.markdown + "\n"
    return (
        f"{explanation.header}\n\n{SUMMARY_HEADING}\n\n{summary.strip()}\n\n"
        f"{explanation.body}\n"
    )


def check_summary(summary: str, explanation: Explanation) -> SummaryCheck:
    """要約に出る要件 ID・数字を、説明書と照合する(全角の数字は半角に直して比べる)。要件 ID の中の
    数字は、数字としては数えない。説明書の見出しの行(節の番号・段階の番号)は照合の元にしない。"""
    known_text = unicodedata.normalize(
        "NFKC",
        "\n".join(
            line for line in explanation.markdown.splitlines() if not line.startswith("#")
        ),
    )
    text = unicodedata.normalize("NFKC", summary)
    known_ids = set(_REQUIREMENT_ID.findall(known_text))
    known_numbers = set(_NUMBER.findall(_REQUIREMENT_ID.sub(" ", known_text)))
    ids = _unique(i for i in _REQUIREMENT_ID.findall(text) if i not in known_ids)
    numbers = _unique(
        n for n in _NUMBER.findall(_REQUIREMENT_ID.sub(" ", text)) if n not in known_numbers
    )
    return SummaryCheck(unknown_requirement_ids=ids, unknown_numbers=numbers)


def _section_request(facts: ChangeRequestFacts) -> str:
    when = facts.background_date.isoformat() if facts.background_date else "(記入なし)"
    memo = _cell(facts.background_memo) or "(記入なし)"
    return "\n".join(
        [
            "## 1. 変更の内容と背景",
            "",
            SECTION_LEAD["request"],
            "",
            facts.content.strip(),
            "",
            "| 背景 | 内容 |",
            "|---|---|",
            f"| 出どころ | {BACKGROUND_SOURCE_LABEL[facts.background_source]} |",
            f"| 日付 | {when} |",
            f"| メモ | {memo} |",
        ]
    )


def _section_targets(facts: ChangeRequestFacts, rows: Mapping[str, Mapping[str, Any]]) -> str:
    lines = ["## 2. 対象の要件", "", SECTION_LEAD["targets"], ""]
    if facts.targets:
        lines += [
            "| 要件 ID | 変更の種類 | 合意済みの文 | 変更後の案 |",
            "|---|---|---|---|",
        ]
        for target in facts.targets:
            agreed = _cell(str(rows.get(target.requirement_id, {}).get("text", "")))
            if target.kind == "delete":
                proposal = DELETED_PROPOSAL
            else:
                proposal = _cell(target.proposed_text) or NO_PROPOSAL
            lines.append(
                f"| {target.requirement_id} | {TARGET_KIND_LABEL[target.kind]} | "
                f"{agreed} | {proposal} |"
            )
    else:
        lines.append("既存の要件は変えません。")
    if facts.adds_requirement:
        lines += ["", "このほかに、新しい要件を足します(内容は1節)。"]
        if facts.origin_labels:
            names = "・".join(display_name(o) for o in facts.origin_labels)
            lines.append(f"新しい要件の予測の起点: {names}。")
    return "\n".join(lines)


def _section_impact(prediction: Prediction, excluded: Sequence[tuple[str, str]]) -> str:
    lines = ["## 4. 影響の一覧", "", SECTION_LEAD["impact"], ""]
    if prediction.items:
        lines.append(
            f"合意済みの版の設計から、見直しが要る要素を{len(prediction.items)}件と予測しました"
            f"({'対象の要件と新しい要件の起点' if prediction.origins else '対象の要件'}からの"
            "つながりをたどったもの)。"
        )
        for place in sorted({i.stage for i in prediction.items}):
            lines += [
                "",
                f"### {PLACE_LABEL.get(place, f'置き場{place}')}",
                "",
                "| 要素 | 理由 | 届いた対象 |",
                "|---|---|---|",
            ]
            for item in (i for i in prediction.items if i.stage == place):
                lines.append(
                    f"| {_cell(display_name(item.label))} | {_cell(item.reason)} | "
                    f"{_reached_by(item)} |"
                )
    else:
        lines.append("見直しが要る要素は予測されませんでした。")
    notes = []
    if excluded:
        names = "・".join(
            f"{_place_name(key)}({EXCLUDED_STATE_LABEL.get(state, state)})"
            for key, state in excluded
        )
        notes.append(
            f"- 合意済みの版に入っていないもの: {names}。その要素はこの一覧に出ません。"
        )
    if prediction.unpredicted_new_requirement:
        notes.append("- 新しい要件を足す分は、つながりの起点が無いため予測に入っていません。")
    if notes:
        lines += ["", *notes]
    return "\n".join(lines)


def _reached_by(item: PredictedItem) -> str:
    """影響の表の「届いた対象」(対象の要件と、新しい要件の起点)。"""
    names = list(item.requirement_ids)
    if item.origin_keys:
        names.append(f"新しい要件(起点 {'・'.join(origin_name(k) for k in item.origin_keys)})")
    return _cell("・".join(names))


def _section_scale(scale: Scale) -> str:
    lines = ["## 3. 変更の規模", "", SECTION_LEAD["scale"]]
    uncountable = [c.label for c in scale.counts if c.uncountable]
    if uncountable:
        lines.append(
            f"{'・'.join(uncountable)}は数えられませんでした(理由は表の下)。この部分の手間の"
            "見込みは、開発者に別に確かめてください。"
        )
    lines += ["", "| 種類 | 数 | 内訳 |", "|---|---|---|"]
    for count in scale.counts:
        number = NO_COUNT if count.count is None else f"{count.count}件"
        lines.append(f"| {count.label} | {number} | {_cell(_breakdown(count, scale))} |")
    # 同じ理由・注記は種類をまとめて1行にする(現れた順)
    remarks: dict[ScaleRemark, list[str]] = {}
    for count in scale.counts:
        for remark in (*count.uncountable, *count.notes):
            remarks.setdefault(remark, []).append(count.label)
    if remarks:
        lines.append("")
        lines += [
            f"- {'・'.join(kinds)}: {_remark_text(remark)}" for remark, kinds in remarks.items()
        ]
    return "\n".join(lines)


def _breakdown(count: ScaleCount, scale: Scale) -> str:
    """規模の表の「内訳」(数えられないときは空。テストは結合・総合に分ける)。"""
    if count.uncountable or not count.names:
        return ""
    if count.kind == "test":
        parts = []
        if scale.integration_tests:
            parts.append(
                f"結合テスト {len(scale.integration_tests)}件({'・'.join(scale.integration_tests)})"
            )
        if scale.system_tests:
            parts.append(
                f"総合テスト {len(scale.system_tests)}件({'・'.join(scale.system_tests)})"
            )
        return "・".join(parts)
    return "・".join(count.names)


def _remark_text(remark: ScaleRemark) -> str:
    return f"{remark.label}({remark.detail})" if remark.detail else remark.label


def _section_questions(
    targets: Sequence[Target],
    rows: Mapping[str, Mapping[str, Any]],
    statuses: Mapping[str, str],
) -> str:
    open_points: dict[str, list[str]] = {}
    assumed: list[tuple[str, str]] = []
    for target in targets:
        row = rows.get(target.requirement_id)
        if row is None:
            continue
        for key in row.get("points", []):
            if is_point_key(key) and statuses.get(key, "open") == "open":
                ids = open_points.setdefault(key, [])
                if target.requirement_id not in ids:
                    ids.append(target.requirement_id)
        if row.get("source") == "assumed":
            assumed.append((target.requirement_id, str(row.get("text", ""))))
    lines = ["## 5. 合意の前に確かめたい点", "", SECTION_LEAD["questions"], ""]
    if not open_points and not assumed:
        lines.append("合意の前に確かめたい点はありません。")
        return "\n".join(lines)
    for key, ids in open_points.items():
        point = point_def(key)
        lines.append(
            f"- 「{point.label}」がまだ決まっていません(関わる要件: {'・'.join(ids)})。"
            f"{_sentence(point.question)}"
        )
    for req_id, text in assumed:
        lines.append(
            f"- {req_id} の文は、AI が補った推測で、まだ確かめていません。この文のとおりで"
            f"よいですか。「{_line(text)}」"
        )
    return "\n".join(lines)


def _sentence(text: str) -> str:
    """問いの例を1文にする(句点・疑問符で終わっていなければ句点を足す)。"""
    text = text.strip()
    return text if text.endswith(("。", "？", "?")) else f"{text}。"


def _place_name(key: str) -> str:
    if key.startswith("stage:") and key[6:].isdigit():
        return PLACE_LABEL.get(int(key[6:]), key)
    return DOCUMENT_LABEL.get(key, key)


def _cell(value: str) -> str:
    """表のセルに入れる文(前後の空白を除き、改行は <br>、縦棒は逃がす)。"""
    return value.strip().replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>")


def _line(value: str) -> str:
    return " ".join(value.split())


def _unique(values: Any) -> tuple[str, ...]:
    found: list[str] = []
    for value in values:
        if value not in found:
            found.append(value)
    return tuple(found)
