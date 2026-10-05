"""LLM(Gemini)の構造化出力に渡す、UML図(構成図・DFD)の出力スキーマ。

詳細設計モードの段階2・4の下書きが使う(段階3の ER は app/detailed_design/data_model_drafting.py の
スキーマを使う)。

ドメインモデル(app/uml/domain)をそのまま構造化出力に使わない理由:
- discriminated union(`notation`・`element_type`)や`= []`・Literalの既定値は、LLMに渡す
  JSON Schemaとして冗長・不安定になる。記法ごとにフラットで全項目必須のスキーマにする。
- DFDのフローはデータ辞書をUUID(`DfdFlow.data_item_id`)で参照するが、LLMはUUIDを知り得ない。
  出力ではデータ項目を名前で参照させ、サービス層が名前→UUIDに解決する(mapper.py)。
- `layer`はレーン割り当ての入力で、ドメインモデルではOptional(手動編集の途中でも
  保存できるように)だが、AI生成では必ず埋めさせたいので出力スキーマ側では必須にする。
"""

from typing import Literal

from pydantic import BaseModel, Field

_ID_DESCRIPTION = "図の中で一意な短いID(例: m1, t1, p1)。他の要素からの参照に使う"


class GeneratedModule(BaseModel):
    id: str = Field(description=_ID_DESCRIPTION)
    name: str = Field(description="モジュール名(ディレクトリ構成に現れる名前)")
    description: str = Field(description="モジュールの責務を1行で")
    layer: str = Field(
        description="モジュールが属する層(例: api, service, repository, model, external)。"
        "同じ層のモジュールには同じ文字列を使う"
    )


class GeneratedDependency(BaseModel):
    id: str = Field(description=_ID_DESCRIPTION)
    source_id: str = Field(description="依存する側のモジュールID")
    target_id: str = Field(description="依存される側のモジュールID")


class ComponentGenerationOutput(BaseModel):
    """コンポーネント図の生成結果。"""

    modules: list[GeneratedModule]
    dependencies: list[GeneratedDependency]


class GeneratedColumn(BaseModel):
    name: str
    type: str = Field(description="データ型(例: UUID, VARCHAR(255), TIMESTAMP)")
    is_primary_key: bool
    is_foreign_key: bool
    nullable: bool


class GeneratedTableRelation(BaseModel):
    id: str = Field(description=_ID_DESCRIPTION)
    source_id: str = Field(description="参照される側(1側)のテーブルID")
    target_id: str = Field(description="外部キーを持つ側のテーブルID")
    relation_type: Literal["one_to_one", "one_to_many", "many_to_many"]


class GeneratedDataItemField(BaseModel):
    name: str
    type: str = Field(description="型(不明な場合は空文字列)")


class GeneratedDataItem(BaseModel):
    name: str = Field(description="データ項目名。既存のデータ辞書にある名前は再利用する")
    fields: list[GeneratedDataItemField]


class GeneratedProcess(BaseModel):
    id: str = Field(description=_ID_DESCRIPTION)
    name: str = Field(description="処理名")
    description: str = Field(description="入力をどう加工して出力にするかを1行で")
    layer: str = Field(
        description="処理を担当するモジュールの層(例: api, service, repository)。"
        "同じ層の処理には同じ文字列を使う"
    )


class GeneratedNode(BaseModel):
    id: str = Field(description=_ID_DESCRIPTION)
    name: str


class GeneratedFlow(BaseModel):
    id: str = Field(description=_ID_DESCRIPTION)
    source_id: str
    target_id: str
    data_item_name: str = Field(description="流れるデータ項目の名前(data_itemsのいずれか)")


class DfdGenerationOutput(BaseModel):
    """処理別DFD(1処理=1枚)の生成結果。"""

    data_items: list[GeneratedDataItem]
    processes: list[GeneratedProcess]
    external_entities: list[GeneratedNode]
    data_stores: list[GeneratedNode]
    flows: list[GeneratedFlow]
