"""変更の説明書の任意の要約(LLM の入力と、要約の状態の決め方。純粋関数)。

docs/external_design.md 2.14節「変更の説明書」。要約はボタンで LLM に作らせ、「未確認」でできる。
未確認の間は出力の md に載らない(プレビューだけ)。人が直してから「確認した」を押すと載る。

- 入力は説明書の md だけ(CR の元の値や設計の全文は渡さない。要約が5節に無いことを書く
  余地を減らし、照合で確かめられる範囲にとどめるため)。
- 状態は保存した`status`(未確認 / 確認済み)と、要約を作った・確かめた時点の5節の fingerprint で
  決める。今の説明書と fingerprint が違えば「古い」(確認済みでも出力に載らない)。確認済みのまま
  古くなった要約は、共有の前に確かめ直すよう開発者に知らせる(`needs_reconfirm`)。
"""

from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

SummaryStatus = Literal["unconfirmed", "confirmed"]
SummaryState = Literal["unconfirmed", "confirmed", "stale"]

SUMMARY_STATE_LABEL: dict[SummaryState, str] = {
    "unconfirmed": "未確認",
    "confirmed": "確認済み",
    "stale": "古い",
}
# 要約の文の上限(LLM の出力はここで切る。人が直すときも同じ上限)
SUMMARY_MAX = 4000
# LLM に渡す説明書の上限(説明書は数十〜百の要素で、ふつうはこれに収まる)
MAX_EXPLANATION_CHARS = 30000

SUMMARY_SYSTEM_PROMPT = """\
あなたはソフトウェア開発の変更要求を、発注者(クライアント)に説明する担当者です。
渡す「変更の説明書」を読み、クライアント向けの要約を日本語で書いてください。

- 3〜6文、400字以内。見出し・箇条書き・表は使わない(地の文だけ)。
- 何を変えるか、なぜか、どのくらい手が入るか(変更の規模の節の数。「数えられない」ものは
  そう書く)、合意の前に確かめたい点があればそれを書く。
- 要件 ID(FR-001 など)と数字は、説明書に書かれているものだけを使う。説明書に無い数字
  (工数・日数・費用など)を推測して書かない。
- 設計の内部の名前(モジュールのパス・関数名)は書かない。
"""


def build_summary_messages(explanation_markdown: str) -> list[BaseMessage]:
    """要約の LLM の入力(説明書だけ)。"""
    return [
        SystemMessage(content=SUMMARY_SYSTEM_PROMPT),
        HumanMessage(content=explanation_markdown[:MAX_EXPLANATION_CHARS]),
    ]


def clip_summary(text: str) -> str:
    """LLM の出力を要約の文にする(前後の空白を除き、上限で切る)。"""
    return text.strip()[:SUMMARY_MAX]


def summary_state(status: str, fingerprint: str, current_fingerprint: str) -> SummaryState:
    """要約の状態。作った・確かめた時点の説明書と今の説明書が違えば「古い」、同じなら保存した
    状態。"""
    if fingerprint != current_fingerprint:
        return "stale"
    return "confirmed" if status == "confirmed" else "unconfirmed"


def needs_reconfirm(status: str, fingerprint: str, current_fingerprint: str) -> bool:
    """確認済みの要約が、その後の説明書の変化で古くなったか(出力に載らないので、共有の前に
    確かめ直す)。"""
    return status == "confirmed" and fingerprint != current_fingerprint
