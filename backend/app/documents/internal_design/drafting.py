"""内部設計書の AI の下書きの入出力(純粋関数)。

AI には要件定義書・外部設計書から、テーブル・API・モジュール・処理別データフローの行と本文の節を
書かせる。処理別データフローの ID は書かせない(`assemble.build_model`が前の版から引き継ぐ)。
"""

from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.documents.internal_design.assemble import (
    ColumnDraft,
    DataFlowDraft,
    DataItemDraft,
    FlowDraft,
    InternalApiDraft,
    InternalDesignDraft,
    ModuleDraft,
    TableDraft,
)

_BODY_GUIDE = "Markdown。見出し(#)は書かない。構造はコードブロックのテキスト図で示してよい"


class GeneratedColumn(BaseModel):
    name: str = Field(description="カラム名")
    type: str = Field(description="データ型")
    constraints: str = Field(default="", description="制約(PK・NOT NULL・FK など。無ければ空)")
    description: str = Field(default="", description="説明")


class GeneratedTable(BaseModel):
    name: str = Field(description="テーブル名(英字の識別子)")
    description: str = Field(default="", description="テーブルの役割(1文)")
    columns: list[GeneratedColumn] = Field(description="全カラム")


class GeneratedInternalApi(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(description="【外部設計書】2.6 の API と同じパス")
    summary: str = Field(description="内部の担当(ルート・サービス)の観点の概要")


class GeneratedModule(BaseModel):
    path: str = Field(description="ファイルのパス。同じ層の似たファイルは`*`でまとめてよい")
    layer: str = Field(description="層(ルート・サービス・リポジトリ・モデルなど)")
    responsibility: str = Field(description="責務(1文)")
    depends_on: list[str] = Field(
        description="主な依存先(一覧の他のモジュールのパス、または外部のライブラリ名)"
    )


class GeneratedFlow(BaseModel):
    source: str = Field(
        description="元。外部実体(利用者・外部サービス)、処理(担当モジュール名)、"
        "データストア(テーブル名)のいずれか"
    )
    data: str = Field(description="流れるデータ")
    transform: str = Field(default="", description="変換(無ければ空)")
    target: str = Field(description="先(元と同じ種類のいずれか)")


class GeneratedDataItem(BaseModel):
    name: str = Field(description="データ項目の名前。同じデータ項目は全処理で同じ名前")
    fields: list[str] = Field(description="フィールド")


class GeneratedDataFlow(BaseModel):
    trigger: str = Field(
        description="API なら`<HTTPメソッド> <パス>`(【外部設計書】2.6 と同じ)、"
        "バッチ処理ならバッチ名"
    )
    flows: list[GeneratedFlow] = Field(description="データの流れ(元 → 先の順)")
    data_items: list[GeneratedDataItem] = Field(description="この処理に登場するデータ項目")


class InternalDesignGenerationOutput(BaseModel):
    """内部設計書の下書きの構造化出力。"""

    architecture: str = Field(
        description="3.1 技術スタック選定・アーキテクチャ方針(フロントエンド / バックエンド / "
        f"データベース / インフラの選定理由と構成方針。{_BODY_GUIDE})"
    )
    data_model_note: str = Field(description=f"3.2 主要エンティティ一覧({_BODY_GUIDE})")
    tables: list[GeneratedTable] = Field(description="3.2 テーブルごとの定義")
    module_policy: str = Field(
        description="3.3 主要処理ロジックの分割方針とディレクトリ構成(ディレクトリ構成は"
        f"コードブロックで示す。{_BODY_GUIDE})"
    )
    apis: list[GeneratedInternalApi] = Field(
        description="3.3 API エンドポイント一覧(【外部設計書】2.6 の API をすべて)"
    )
    modules: list[GeneratedModule] = Field(
        description="3.3 モジュール一覧(ファイル単位の責務。スキーマ・設定のような定型の"
        "ファイルは省くか1行にまとめる)"
    )
    dataflows: list[GeneratedDataFlow] = Field(
        description="3.3 処理別データフロー(API・バッチ処理ごとに1つ)"
    )
    error_policy: str = Field(
        description="3.4 例外処理・エラーハンドリング・ログ設計(共通エラーレスポンス形式、"
        f"例外検知・ログ出力方針。{_BODY_GUIDE})"
    )


SYSTEM_PROMPT = (
    "あなたは優秀なリードエンジニア・データベース設計者です。【要件定義書】および【外部設計書】に"
    "基づき、システム内部の構造・ロジックに関する内部設計書の中身を、テーブル・API・モジュール・"
    "処理別データフローの行と、説明の節として書きます。\n"
    "規則:\n"
    "- 【外部設計書】2.6 の API は、すべて API エンドポイント一覧に入れ、それぞれに処理別データ"
    "フローを1つ書く。バッチ処理があれば、それも処理別データフローにする\n"
    "- 処理別データフローの元・先は、外部実体・処理(モジュール一覧の担当モジュール名)・"
    "データストア(テーブル名)のいずれかにする\n"
    "- テーブルは全カラムを書く\n"
    "- 要件定義書に無い技術・外部サービスを創作しない\n"
    "- 一覧性の高い情報は行に、説明は本文の節に書く。本文に見出し(#)は書かない"
)


def build_internal_design_messages(
    requirements_md: str, external_design_md: str
) -> list[BaseMessage]:
    human = f"## 要件定義書\n{requirements_md}\n\n## 外部設計書\n{external_design_md}"
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=human)]


def to_draft(output: InternalDesignGenerationOutput) -> InternalDesignDraft:
    return InternalDesignDraft(
        architecture=output.architecture,
        data_model_note=output.data_model_note,
        tables=tuple(
            TableDraft(
                t.name,
                t.description,
                tuple(
                    ColumnDraft(c.name, c.type, c.constraints, c.description) for c in t.columns
                ),
            )
            for t in output.tables
        ),
        module_policy=output.module_policy,
        apis=tuple(InternalApiDraft(a.method, a.path, a.summary) for a in output.apis),
        modules=tuple(
            ModuleDraft(m.path, m.layer, m.responsibility, tuple(m.depends_on))
            for m in output.modules
        ),
        dataflows=tuple(
            DataFlowDraft(
                d.trigger,
                tuple(FlowDraft(f.source, f.data, f.transform, f.target) for f in d.flows),
                tuple(DataItemDraft(i.name, tuple(i.fields)) for i in d.data_items),
            )
            for d in output.dataflows
        ),
        error_policy=output.error_policy,
    )
