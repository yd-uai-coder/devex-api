"""変更要求(CR)の選択肢(案)の LLM の下書き(入力・構造化出力の形・照合・状態。純粋関数)。

docs/external_design.md 2.16節「説明とすり合わせ」。下書きはボタンで LLM に作らせ、「未確認」で
できる。下書きは説明書に載らない。開発者が見比べて「取り込む」と、案が CR の`options`に写る
(取り込み済み)。取り込んだ案は開発者が書いた案と同じ扱いで、直すのも取り込んだ後の案。

- 入力は説明書の md だけ(要約と同じ方針。照合で確かめられる範囲にとどめるため)。
- 照合は、案の規則(数・名前・結果・おすすめ)と、案の対象が CR の対象の中にあるか(取り込みを
  止める)と、文に出る要件 ID・数字のうち説明書に無いもの(要約と同じ check_summary。人が判断
  するので取り込みは止めない)。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.change_requests.explanation import Explanation, check_summary
from app.change_requests.options import (
    OPTION_NAME_MAX,
    OPTION_OUTCOME_MAX,
    OPTIONS_MAX,
    RECOMMEND_REASON_MAX,
    ChangeOptions,
    normalize_options,
    option_target_problems,
    options_from_rows,
    options_to_json,
)
from app.change_requests.summary import MAX_EXPLANATION_CHARS
from app.change_requests.targets import Target, TargetKind

OptionDraftStatus = Literal["unconfirmed", "adopted"]
OPTION_DRAFT_STATUS_LABEL: dict[OptionDraftStatus, str] = {
    "unconfirmed": "未確認",
    "adopted": "取り込み済み",
}
# 変更後の案の文の上限(スキーマの PROPOSED_TEXT_MAX と同じ)
_PROPOSED_TEXT_MAX = 2000

OPTION_DRAFT_SYSTEM_PROMPT = """\
あなたはソフトウェア開発の変更要求について、発注者(クライアント)が選べる進め方の案を考える
担当者です。渡す「変更の説明書」を読み、2〜3の案を日本語で作ってください。

- 案ごとに、名前(20字程度)・その案で変える要件(説明書の「対象の要件」の要件 ID の中から選ぶ。
  種類は内容の変更か削除。内容の変更なら変更後の要件の文の案)・結果(手間・影響・トレードオフを
  2〜3文)を書く。
- 案どうしは、変える要件の範囲か、変更後の文の中身で違いが分かるようにする。
- おすすめの案の番号(1始まり)と、その理由を1〜2文で書く。
- 要件 ID と数字は、説明書に書かれているものだけを使う。説明書に無い数字(工数・日数・費用など)を
  推測して書かない。規模の数は Devex が案ごとに数えるので、結果に書かなくてよい。
- 設計の内部の名前(モジュールのパス・関数名)は書かない。
"""


class OptionDraftTargetOut(BaseModel):
    """下書きの案の対象1つ。"""

    requirement_id: str = Field(description="変える要件の ID(説明書の対象の要件の中から)")
    kind: TargetKind = Field(default="content", description="content=内容の変更 / delete=削除")
    proposed_text: str = Field(default="", description="内容の変更なら、変更後の要件の文の案")


class OptionDraftItemOut(BaseModel):
    """下書きの案1つ。"""

    name: str = Field(description="案の名前(20字程度)")
    targets: list[OptionDraftTargetOut] = Field(description="この案で変える要件")
    outcome: str = Field(description="結果(手間・影響・トレードオフを2〜3文)")


class OptionDraftOut(BaseModel):
    """選択肢の下書きの構造化出力。"""

    options: list[OptionDraftItemOut] = Field(description="2〜3の案")
    recommended: int | None = Field(default=None, description="おすすめの案の番号(1始まり)")
    reason: str = Field(default="", description="おすすめの理由(1〜2文)")


@dataclass(frozen=True)
class OptionDraftCheck:
    """下書きの照合の結果。`problems`は案の規則と対象の問題(取り込めない)、`unknown_*`は文に出る
    要件 ID・数字のうち説明書に無いもの(取り込みは止めない)。"""

    problems: tuple[str, ...]
    unknown_requirement_ids: tuple[str, ...]
    unknown_numbers: tuple[str, ...]

    @property
    def adoptable(self) -> bool:
        return not self.problems

    @property
    def ok(self) -> bool:
        return not self.problems and not self.unknown_requirement_ids and not self.unknown_numbers


def build_option_draft_messages(explanation_markdown: str) -> list[BaseMessage]:
    """下書きの LLM の入力(説明書だけ)。"""
    return [
        SystemMessage(content=OPTION_DRAFT_SYSTEM_PROMPT),
        HumanMessage(content=explanation_markdown[:MAX_EXPLANATION_CHARS]),
    ]


def draft_to_options(out: OptionDraftOut) -> ChangeOptions:
    """構造化出力を案にする(案は上限の数まで、文は上限で切る。規則に合うかは照合で見る)。"""
    return options_from_rows(
        (
            (
                item.name.strip()[:OPTION_NAME_MAX],
                (
                    Target(
                        t.requirement_id.strip(),
                        t.kind,
                        "" if t.kind == "delete" else t.proposed_text.strip()[:_PROPOSED_TEXT_MAX],
                    )
                    for t in item.targets
                ),
                item.outcome.strip()[:OPTION_OUTCOME_MAX],
            )
            for item in out.options[:OPTIONS_MAX]
        ),
        out.recommended,
        out.reason.strip()[:RECOMMEND_REASON_MAX],
    )


def draft_to_json(options: ChangeOptions) -> dict[str, Any]:
    """下書きの保存の形(案の保存の形と同じ。案が0件でも空の一覧で持つ)。"""
    return options_to_json(options) or {"items": [], "recommended": None, "reason": ""}


def check_option_draft(
    options: ChangeOptions,
    explanation: Explanation,
    targets: Sequence[Target],
    adds_requirement: bool,
) -> OptionDraftCheck:
    """下書きを照合する(案の規則・CR の対象・説明書に無い要件 ID と数字)。"""
    problems: list[str] = []
    try:
        normalized = normalize_options(options)
    except ValueError as exc:
        problems.append(str(exc))
        normalized = options
    problems += option_target_problems(normalized, targets, adds_requirement)
    texts = [options.reason]
    for option in options.items:
        texts += [option.name, option.outcome, *(t.proposed_text for t in option.targets)]
    check = check_summary("\n".join(texts), explanation)
    return OptionDraftCheck(
        problems=tuple(problems),
        unknown_requirement_ids=check.unknown_requirement_ids,
        unknown_numbers=check.unknown_numbers,
    )
