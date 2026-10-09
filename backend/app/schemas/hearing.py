import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.hearing import PointKey, PointKind, PointStatus, Scale

ChatSender = Literal["user", "ai", "intake", "others", "attachment"]


class HearingMessageRequest(BaseModel):
    """チャットヒアリングへのメッセージ送信APIのリクエストボディ。"""

    message: str


class ChatHistoryRead(BaseModel):
    """チャット履歴1件をAPIレスポンスとして返す際のスキーマ。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender: ChatSender
    message: str
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
