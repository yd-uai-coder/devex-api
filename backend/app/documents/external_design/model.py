"""外部設計書の意味モデル(`generated_documents.model`に JSON で持つ)。

一覧(画面・API・外部連携)は行にし、各行に関係する要件ID(要件定義書の`FR-001`など)を持つ。
説明(システム構成・画面遷移・データ入出力)は本文の文字列、画面ごとの UI 仕様は画面の行の文字列。
画面ID(`SCR-001`)は再生成しても同じ画面は同じ ID を引き継ぐ(`assemble.build_model`)。
"""

from pydantic import BaseModel, Field

from app.documents.ids import seq_id
from app.documents.requirements.model import Priority

SCREEN_PREFIX = "SCR"
# 本文(リクエストボディ)を持つはずのメソッド
BODY_METHODS = ("POST", "PUT", "PATCH")
# 本文の要らない POST など(生成・ログアウトの操作)の本文の欄に書く値。空欄と区別する
NO_BODY = "なし"


class ScreenRow(BaseModel):
    """2.2 の画面1行。`ui_spec`は 2.3 の`### SCR-001: 画面名`の下の本文。"""

    id: str
    name: str
    role: str = ""
    priority: Priority = "Must"
    req_ids: list[str] = Field(default_factory=list)
    ui_spec: str = ""


class ApiRow(BaseModel):
    """2.6 の API 1行。`screens`は画面ID(2.2 に無い ID もそのまま残し、検証で指摘する)。"""

    method: str
    path: str
    summary: str = ""
    screens: list[str] = Field(default_factory=list)
    req_ids: list[str] = Field(default_factory=list)
    body: str = ""
    response: str = ""
    errors: str = ""

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"


class IntegrationRow(BaseModel):
    """2.4 の外部連携1行。"""

    service: str
    purpose: str = ""
    auth: str = ""
    timing: str = ""
    req_ids: list[str] = Field(default_factory=list)


class ExternalDesignModel(BaseModel):
    """外部設計書1版の中身。"""

    overview: str = ""  # 2.1 システム構成概要(md の本文)
    transition: str = ""  # 2.2 画面遷移(テキスト図。md ではコードブロックに入れる)
    data_io: str = ""  # 2.5 データ入出力仕様(md の本文)
    screens: list[ScreenRow] = Field(default_factory=list)
    apis: list[ApiRow] = Field(default_factory=list)
    integrations: list[IntegrationRow] = Field(default_factory=list)
    # 生成した時点の要件定義書の要件ID と、Must の機能要件ID。検証と付録はこれを読む
    # (要件定義書の「採用にする」は版を変えないので、生成時点の写しで十分)
    known_req_ids: list[str] = Field(default_factory=list)
    must_fr_ids: list[str] = Field(default_factory=list)
    # 画面ID の次の番号(消えた ID を使い回さないため)
    next_seq: dict[str, int] = Field(default_factory=dict)


def screen_id(seq: int) -> str:
    return seq_id(SCREEN_PREFIX, seq)
