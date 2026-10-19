"""変更要求(CR)の説明書(5節の md)の組み立てと、任意の要約の照合(純粋関数)。

docs/external_design.md 2.14節「変更の説明書」。合意の前にクライアントへ見せる md を、CR の値・
基準の合意済みの版(束)の写し・影響の予測から決定的に組み立てる。LLM は使わず、同じ入力からは
いつも同じ md になる。

1. 変更の内容と背景
2. 対象の要件(合意済みの文と変更後の案)
3. 影響の一覧(置き場ごと・理由・届いた対象)
4. 関わる未決事項(対象の要件の根拠の観点のうち未決のもの・推測の要件)
5. 影響を受ける作業単位とテストの数

- 4節は、今の観点の表でなく束の要件定義書の写し(`point_statuses`・`rows[].source`)から読む
  (合意した版と同じものを示し、後でヒアリングが進んでも説明書が変わらないように)。
- `fingerprint`は5節の md の sha256。要約を作った・確かめた時点の値と比べ、違えば要約は「古い」。
- 要約の照合は、要約に出る要件 ID と数字のうち、5節に出ないものを返す。
"""

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.change_requests.prediction import Prediction
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
TEST_SPEC_KEY = "test_spec"
DELETED_PROPOSAL = "(削除)"
NO_PROPOSAL = "(1節の内容を参照)"
SUMMARY_HEADING = "## 要約"

_REQUIREMENT_ID = re.compile(r"\b(?:FR|NFR|CON)-\d+\b")
# 数字(英字に続く・英字が続くものは名前の一部とみなして数えない。`E2E`・`S3`など)
_NUMBER = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?(?![A-Za-z\d])")


@dataclass(frozen=True)
class ChangeRequestFacts:
    """説明書の1節に載る CR の値(DB の行から写したもの)。"""

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


@dataclass(frozen=True)
class AffectedUnits:
    """影響を受ける作業単位(表示名の一覧)とテスト(ID の一覧)。`has_test_spec`が偽なら、束に
    テスト仕様書が無く、テストは数えていない。"""

    units: tuple[str, ...]
    integration_tests: tuple[str, ...]
    system_tests: tuple[str, ...]
    has_test_spec: bool


@dataclass(frozen=True)
class Explanation:
    """説明書の5節。`header`は題と基準の行、`body`は1〜5節。"""

    header: str
    body: str
    units: AffectedUnits

    @property
    def markdown(self) -> str:
        return f"{self.header}\n\n{self.body}"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.markdown.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SummaryCheck:
    """要約の照合の結果。5節に出ない要件 ID と数字(出た順・重なりなし)。"""

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
) -> Explanation:
    """CR の値・予測・束の写し(key → model。入っているものだけ)・束に入っていない文書と段階
    (key, 状態)から、説明書の5節を組み立てる。"""
    requirements = snapshots.get(REQUIREMENTS_KEY) or {}
    rows = {str(row["id"]): row for row in requirements.get("rows", []) if row.get("id")}
    units = affected_units(facts.targets, prediction, snapshots.get(TEST_SPEC_KEY))
    label = f"CR-{facts.number}"
    header = "\n".join(
        [
            f"# 変更の説明書 {label}: {_line(facts.title)}",
            "",
            f"- 基準の合意済みの版: B-{facts.baseline_number}",
            f"- CR の状態: {STATUS_LABEL[facts.status]}",
        ]
    )
    sections = [
        _section_request(facts),
        _section_targets(facts, rows),
        _section_impact(prediction, excluded),
        _section_open_points(facts.targets, rows, requirements.get("point_statuses", {})),
        _section_units(units),
    ]
    return Explanation(header=header, body="\n\n".join(sections), units=units)


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
    """要約に出る要件 ID・数字を、5節と照合する(全角の数字は半角に直して比べる)。要件 ID の中の
    数字は、数字としては数えない。5節の見出しの行(節の番号・段階の番号)は照合の元にしない。"""
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


def affected_units(
    targets: Sequence[Target],
    prediction: Prediction,
    test_spec: Mapping[str, Any] | None,
) -> AffectedUnits:
    """予測から作業単位(段階7のタスク・段階8の手順書。単位のキーで重なりを除く)の ID を集め、
    束のテスト仕様書の写しから、予測の処理か対象の要件に当たるテストを集める。単位の ID は要素の
    ラベルの先頭(`M-01-T02 予約の登録`・`M-01-T02 の手順書`)。"""
    unit_labels: dict[str, str] = {}
    for item in prediction.items:
        prefix, _, unit_key = item.key.partition(":")
        if prefix in ("task", "unit"):
            unit_labels.setdefault(unit_key, item.label.split(" ", 1)[0])
    function_ids = {
        item.key.partition(":")[2] for item in prediction.items if item.kind == "function"
    }
    requirement_ids = {t.requirement_id for t in targets}

    def hits(cases: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
        return tuple(
            str(case.get("id", ""))
            for case in cases
            if function_ids.intersection(case.get("functions", []))
            or requirement_ids.intersection(case.get("req_ids", []))
        )

    spec = test_spec or {}
    return AffectedUnits(
        units=tuple(unit_labels[k] for k in sorted(unit_labels)),
        integration_tests=hits(spec.get("integration", [])),
        system_tests=hits(spec.get("system", [])),
        has_test_spec=test_spec is not None,
    )


def _section_request(facts: ChangeRequestFacts) -> str:
    when = facts.background_date.isoformat() if facts.background_date else "(記入なし)"
    memo = _cell(facts.background_memo) or "(記入なし)"
    return "\n".join(
        [
            "## 1. 変更の内容と背景",
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
    lines = ["## 2. 対象の要件", ""]
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
    return "\n".join(lines)


def _section_impact(prediction: Prediction, excluded: Sequence[tuple[str, str]]) -> str:
    lines = ["## 3. 影響の一覧", ""]
    if prediction.items:
        lines.append(
            f"合意済みの版の設計から、見直しが要る要素を{len(prediction.items)}件と予測しました"
            "(対象の要件からのつながりをたどったもの)。"
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
                    f"| {_cell(item.label)} | {_cell(item.reason)} | "
                    f"{'・'.join(item.requirement_ids)} |"
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


def _section_open_points(
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
    lines = ["## 4. 関わる未決事項", ""]
    if not open_points and not assumed:
        lines.append("対象の要件に関わる未決事項はありません。")
        return "\n".join(lines)
    if open_points:
        lines += [
            "対象の要件の根拠のうち、ヒアリングで決まっていない観点です。",
            "",
            "| 観点 | 関わる要件 |",
            "|---|---|",
        ]
        lines += [
            f"| {point_def(key).label} | {'・'.join(ids)} |" for key, ids in open_points.items()
        ]
    if assumed:
        if open_points:
            lines.append("")
        lines += [
            "対象の要件のうち、AI が補った推測の要件です(利用者に確かめていません)。",
            "",
            "| 要件 ID | 文 |",
            "|---|---|",
        ]
        lines += [f"| {req_id} | {_cell(text)} |" for req_id, text in assumed]
    return "\n".join(lines)


def _section_units(units: AffectedUnits) -> str:
    lines = ["## 5. 影響を受ける作業単位とテストの数", ""]
    unit_names = "・".join(units.units)
    lines.append(
        f"- 作業単位: {len(units.units)}件" + (f"({unit_names})" if units.units else "")
    )
    if units.has_test_spec:
        total = len(units.integration_tests) + len(units.system_tests)
        lines.append(
            f"- テスト: {total}件(結合テスト {len(units.integration_tests)}件・"
            f"総合テスト {len(units.system_tests)}件)"
        )
        ids = [*units.integration_tests, *units.system_tests]
        if ids:
            lines.append(f"  - {'・'.join(ids)}")
    else:
        lines.append("- テスト: 合意済みの版にテスト仕様書が無いため、数えていません。")
    return "\n".join(lines)


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
