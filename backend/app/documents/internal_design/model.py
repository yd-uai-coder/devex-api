"""内部設計書の意味モデル(簡易ドキュメントモード。`generated_documents.model`に JSON で持つ)。

一覧(テーブルと列・API・モジュール・処理別データフロー)は行にし、説明の節(技術スタック・
データモデルの概要・処理の分割方針・エラーとログ)は本文の文字列にする。md は今までの内部設計書と
同じ見出しで組み立てる(`markdown.render_internal_design`)。

処理別データフローの ID(`DF-1`)は AI に書かせず、前の版の同じ処理(同じ API・同じバッチ名)の
ID を引き継ぐ(`assemble.build_model`)。消えた ID は使い回さない。
"""

from pydantic import BaseModel, Field

from app.detailed_design.api_list import endpoint_key, trigger_key
from app.detailed_design.structure import ModuleRow

DATAFLOW_PREFIX = "DF"


class ColumnRow(BaseModel):
    """テーブルの列1つ。"""

    name: str
    type: str = ""
    constraints: str = ""
    description: str = ""


class TableDef(BaseModel):
    """3.2 のテーブル1つ(`### テーブル: <name>`と列の表)。"""

    name: str
    description: str = ""
    columns: list[ColumnRow] = Field(default_factory=list)


class InternalApiRow(BaseModel):
    """3.3 の API エンドポイント一覧の1行(内部の担当の観点の概要)。"""

    method: str
    path: str
    summary: str = ""

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def key(self) -> str:
        return endpoint_key(self.method, self.path)


class FlowRow(BaseModel):
    """処理別データフローの流れの表の1行(元 → 先)。"""

    source: str
    data: str = ""
    transform: str = ""
    target: str = ""


class DataItem(BaseModel):
    """処理に登場するデータ項目(`- データ項目: <name>(<fields>)`)。"""

    name: str
    fields: list[str] = Field(default_factory=list)


class DataFlowRow(BaseModel):
    """3.3 の処理別データフロー1つ。`trigger`は API(`POST /api/v1/x`)かバッチ名。"""

    id: str
    trigger: str
    flows: list[FlowRow] = Field(default_factory=list)
    data_items: list[DataItem] = Field(default_factory=list)

    @property
    def match_key(self) -> str:
        """前の版の同じ処理を探すキー(API は照合のキー、バッチは名前)。"""
        return trigger_key(self.trigger) or f"batch:{' '.join(self.trigger.split())}"


class InternalDesignModel(BaseModel):
    """内部設計書1版の中身。"""

    architecture: str = ""  # 3.1 技術スタック選定・アーキテクチャ方針
    data_model_note: str = ""  # 3.2 の主要エンティティ一覧(テーブルの前の本文)
    tables: list[TableDef] = Field(default_factory=list)
    module_policy: str = ""  # 3.3 の処理の分割方針とディレクトリ構成
    apis: list[InternalApiRow] = Field(default_factory=list)
    # モジュール一覧(段階4と同じ行の形。簡易モードは処理の欄を使わない)
    modules: list[ModuleRow] = Field(default_factory=list)
    dataflows: list[DataFlowRow] = Field(default_factory=list)
    error_policy: str = ""  # 3.4 例外処理・エラーハンドリング・ログ設計
    # 生成した時点の外部設計書の API(`POST /api/v1/x`)。検証が読む
    external_apis: list[str] = Field(default_factory=list)
    # 処理別データフローの次の番号(消えた ID を使い回さないため)
    next_dataflow_seq: int = 1


def dataflow_id(seq: int) -> str:
    """処理別データフローの ID(`DF-1`。今までの内部設計書と同じくゼロ埋めしない)。"""
    return f"{DATAFLOW_PREFIX}-{seq}"


def dataflow_seq(value: str) -> int | None:
    """`DF-1`・`df-01`の番号。形が違えば None。"""
    prefix, sep, number = value.strip().upper().partition("-")
    if prefix != DATAFLOW_PREFIX or not sep or not number.isdigit():
        return None
    return int(number)
