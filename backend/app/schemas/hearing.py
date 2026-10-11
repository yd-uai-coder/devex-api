import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.hearing import PointKey, PointKind, PointStatus, Scale

ChatSender = Literal["user", "ai", "intake", "others", "attachment"]


class CardAnswer(BaseModel):
    """選択肢カード1枚への回答(選んだ選択肢の番号は0始まり)。"""

    card_id: str
    option: int


class HearingMessageRequest(BaseModel):
    """チャットヒアリングへのメッセージ送信APIのリクエストボディ。

    `card_answers`は、最新のAIの発言の選択肢カードへの回答。選んだ内容と`message`をまとめて
    1つの発言にする。どちらか一方は要る(「任意を続ける」だけの送信は`continue_optional`だけでよい)。
    """

    message: str = ""
    card_answers: list[CardAnswer] = Field(default_factory=list)
    # 必須がそろった後も、任意の観点のヒアリングを続ける
    # (画面の「任意項目についてヒアリングを続ける」)
    continue_optional: bool = False

    @model_validator(mode="after")
    def _require_message_or_answers(self) -> "HearingMessageRequest":
        if not self.message.strip() and not self.card_answers and not self.continue_optional:
            raise ValueError("message・card_answers・continue_optional のどれかが必要です")
        return self


class CardOptionRead(BaseModel):
    label: str
    result: str


class CardRead(BaseModel):
    """チャット画面に出す選択肢カード(`chat_histories.cards`の1要素)。"""

    id: str
    point_key: PointKey
    label: str
    question: str
    options: list[CardOptionRead]
    recommended: int
    reason: str
    selected: int | None = None


class ChatHistoryRead(BaseModel):
    """チャット履歴1件をAPIレスポンスとして返す際のスキーマ。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender: ChatSender
    message: str
    cards: list[CardRead] | None = None
    created_at: datetime


class JudgedPointOut(BaseModel):
    """判定のLLMが返す観点1つ。`evidence`はLLMに見せた発言の番号([#n]のn)。"""

    key: PointKey = Field(description="観点のキー")
    status: PointStatus = Field(
        description=(
            "decided=ユーザーが自分の言葉で決めた / adopted=AIの提案にユーザーが同意した / "
            "not_applicable=このシステムには要らないとユーザーが答えた / open=まだ決まっていない"
        )
    )
    summary: str = Field(default="", description="決めたことを1文で。openなら空でよい")
    evidence: list[int] = Field(
        default_factory=list, description="根拠にしたユーザー側の発言の番号([#n]のn)"
    )


class HearingPointsJudgement(BaseModel):
    """ヒアリングの観点ごとの状態を、LLMに構造化出力させるためのスキーマ。完了の判定は
    含めない(必須の観点がそろったかはコードが決める。app/hearing/points.py)。"""

    scale: Scale | None = Field(
        default=None,
        description=(
            "規模の観点が決まったときだけ: "
            "personal=個人・小規模 / internal=社内 / public=公開サービス"
        ),
    )
    points: list[JudgedPointOut] = Field(default_factory=list)


class CardOptionOut(BaseModel):
    label: str = Field(description="選択肢(短く)")
    result: str = Field(description="選んだ結果。コスト・手間・後から変えられるかを1文で")


class CardOut(BaseModel):
    """返信のLLMが返す選択肢カード1枚。"""

    point_key: PointKey = Field(description="カードにする観点のキー(指定された候補から選ぶ)")
    question: str = Field(
        description="場面で聞く問い(例: 夜中や休日に止まっていたら、どのくらいで困りますか)"
    )
    options: list[CardOptionOut] = Field(description="選択肢を2〜4個")
    recommended: int = Field(description="おすすめの選択肢の番号(0始まり)")
    reason: str = Field(description="おすすめの理由を1文で")


class HearingReplyOut(BaseModel):
    """ヒアリングの返信を、LLMに構造化出力させるためのスキーマ(本文と選択肢カード)。"""

    message: str = Field(description="利用者への返信の本文")
    cards: list[CardOut] = Field(
        default_factory=list,
        description="選択肢で答えやすい観点の問い。候補が無い・選択肢に向かない観点は出さない",
    )


class HearingPointUpdate(BaseModel):
    """観点の表の画面から観点を変える本文。今は「対象外(後で決める)」にすることだけを受ける。"""

    status: Literal["not_applicable"]


class EvidenceRead(BaseModel):
    message_id: uuid.UUID
    label: str  # 初期入力 / 添付資料 / 発言N


class HearingPointRead(BaseModel):
    key: PointKey
    kind: PointKind
    label: str
    question: str
    required: bool
    status: PointStatus
    summary: str
    evidence: list[EvidenceRead]


class HearingPointsRead(BaseModel):
    """チャット画面の観点の表。

    - `legacy`: 観点の表を持たず、以前の5条件の判定だけがあるプロジェクト(次の発言から
      観点の表で判定する)
    - `stale`: 直近の発言の判定に失敗し、表がその発言を反映していない
    """

    scale: Scale | None
    legacy: bool
    stale: bool
    is_sufficient: bool
    points: list[HearingPointRead]
