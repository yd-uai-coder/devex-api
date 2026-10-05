"""段階2(データフロー)のAIの下書きの入出力(純粋関数。docs/external_design.md 2.7節)。

1回の生成で、次の2種類を順に呼ぶ:

- 処理概要表: 段階1の全処理について、入力/処理内容/出力を1行ずつ(LLM 1回)。
- 機能グループの DFD: 人が選んだグループごとに1枚(1グループで LLM 1回)。入力は、そのグループの
  処理と、先に作った処理概要表の行と、既存のデータ辞書。

DFD の処理の箱は段階1の処理IDにする(詳細設計書の02章で、図の箱と01章の行が同じIDで対応する
ように)。AIには`function_id`で処理を指させ、名前と ID はここで機能一覧から決める。そのうえで
UML図の出力スキーマ`DfdGenerationOutput`の形に組み替えるので、データ項目の名前の解決と
意味モデルへの変換は`app/uml/generation/mapper.py`をそのまま使える。
"""

from collections.abc import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.data_flow import ProcessSummaryDraft, ProcessSummaryRow
from app.detailed_design.function_list import FunctionRow
from app.detailed_design.prompt_rules import NAMING_RULES
from app.uml.generation.prompts import ExistingDataItem
from app.uml.generation.schemas import (
    DfdGenerationOutput,
    GeneratedDataItem,
    GeneratedFlow,
    GeneratedNode,
    GeneratedProcess,
)
from app.uml.validation.structural import MAX_ELEMENTS


class GeneratedSummary(BaseModel):
    """AIが下書きする処理概要表の1行(構造化出力のスキーマ)。"""

    function_id: str = Field(description="【機能一覧】の処理ID(F-01など)をそのまま書く")
    input: str = Field(description="処理が受け取るもの(画面の入力・前提のデータ)を短く")
    process: str = Field(description="処理内容。検証・判定・保存・外部呼び出しの順に1〜3文で")
    output: str = Field(description="処理の結果(画面へ返すもの・保存するもの)を短く")


class ProcessSummaryGenerationOutput(BaseModel):
    """処理概要表の構造化出力。"""

    rows: list[GeneratedSummary]


class GeneratedGroupProcess(BaseModel):
    """機能グループの DFD の処理の箱1つ。処理は【このグループの処理】の処理IDで指す。"""

    function_id: str = Field(description="【このグループの処理】の処理ID(F-01など)")
    description: str = Field(description="入力をどう加工して出力にするかを1行で")
    layer: str = Field(
        description="図のレーン(列)の名前。処理の段階(受け付け・対話・生成など)を短く。"
        "同じ段階の処理には同じ文字列を使う"
    )


class GroupDfdGenerationOutput(BaseModel):
    """機能グループの DFD 1枚の構造化出力。"""

    data_items: list[GeneratedDataItem]
    processes: list[GeneratedGroupProcess]
    external_entities: list[GeneratedNode]
    data_stores: list[GeneratedNode]
    flows: list[GeneratedFlow]


SUMMARY_SYSTEM_PROMPT = (
    "あなたは詳細設計の担当者です。【機能一覧】の処理ごとに、処理概要表"
    "(入力/処理内容/出力)を1行ずつ書きます。\n"
    "規則:\n"
    "- 【機能一覧】の全ての処理を1行ずつ含め、function_id は処理IDをそのまま書く\n"
    "- 入力・出力は、データの名前(利用者の入力・保存するデータ・返す結果)で短く書く\n"
    "- 処理内容は、検証・判定・保存・外部サービスの呼び出しを、行う順に1〜3文で書く\n"
    "- 【要件定義書】に書かれていない仕様を創作しない"
    + NAMING_RULES
)

DFD_SYSTEM_PROMPT = (
    "あなたはシステムアナリストです。【このグループの処理】をまとめた1枚のデータフロー図(DFD)の"
    "意味モデルを作成します。\n"
    "規則:\n"
    "- processes は【このグループの処理】の処理を1つずつ、function_id で指す"
    "(グループに無い処理IDは使わない)\n"
    "- external_entities は利用者・外部サービス、data_stores は永続化するデータ"
    "(テーブルになるもの。名前は英小文字の複数形、例: reservations)とする\n"
    "- flows は必ず処理を介す(ストア同士・外部実体とストアを直接つながない)。"
    "全ての処理に入力と出力を1つ以上持たせる\n"
    "- 全てのフローに data_item_name を付け、data_items で定義する。"
    "【既存のデータ辞書】にある名前はそのまま再利用し、新しいデータ項目だけを追加する\n"
    f"- 要素(ノード)は合計{MAX_ELEMENTS}個以内に収める\n"
    "- IDは図の中で一意にし、フローの source_id/target_id は、処理なら処理ID、"
    "それ以外は定義済みのIDを指す\n"
    "- 【処理概要表】【要件定義書】に書かれていない要素を創作しない"
    + NAMING_RULES
)


def _function_lines(functions: Sequence[FunctionRow]) -> str:
    return "\n".join(
        f"- {f.id} {f.name}(種別: {f.kind}、トリガー: {f.trigger or 'なし'}、"
        f"機能グループ: {f.group}){f.summary}"
        for f in functions
    )


def build_summary_messages(
    functions: Sequence[FunctionRow], requirements: str
) -> list[BaseMessage]:
    """処理概要表の下書きの入力。段階1の全処理と、要件定義書の全文を渡す。"""
    return [
        SystemMessage(content=SUMMARY_SYSTEM_PROMPT),
        HumanMessage(
            content=f"## 機能一覧\n{_function_lines(functions)}\n\n## 要件定義書\n{requirements}"
        ),
    ]


def to_summary_drafts(output: ProcessSummaryGenerationOutput) -> list[ProcessSummaryDraft]:
    """構造化出力を、merge_summaries の入力に変える。"""
    return [
        ProcessSummaryDraft(
            function_id=row.function_id, input=row.input, process=row.process, output=row.output
        )
        for row in output.rows
    ]


def build_group_dfd_messages(
    group: str,
    functions: Sequence[FunctionRow],
    summaries: Sequence[ProcessSummaryRow],
    requirements: str,
    data_items: Sequence[ExistingDataItem] = (),
) -> list[BaseMessage]:
    """機能グループの DFD の下書きの入力。グループの処理・その処理概要表の行・要件定義書・
    既存のデータ辞書を渡す(データ項目の名前を、グループをまたいでそろえるため)。"""
    ids = {f.id for f in functions}
    summary_lines = "\n".join(
        f"- {s.function_id}: 入力={s.input} / 処理={s.process} / 出力={s.output}"
        for s in summaries
        if s.function_id in ids
    )
    dictionary = "\n".join(f"- {item.name}({', '.join(item.field_names)})" for item in data_items)
    content = "\n\n".join(
        [
            f"## 機能グループ\n{group}",
            f"## このグループの処理\n{_function_lines(functions)}",
            f"## 処理概要表\n{summary_lines or '(まだありません)'}",
            f"## 既存のデータ辞書\n{dictionary or '(まだありません)'}",
            f"## 要件定義書\n{requirements}",
        ]
    )
    return [SystemMessage(content=DFD_SYSTEM_PROMPT), HumanMessage(content=content)]


def to_dfd_output(
    output: GroupDfdGenerationOutput, functions: Sequence[FunctionRow]
) -> DfdGenerationOutput:
    """グループの DFD の出力を、`DfdGenerationOutput`に組み替える。

    - 処理の箱の ID は処理ID、名前は「F-01 名称」にする(名前は機能一覧から取る)。
    - グループに無い処理ID・同じ処理IDの2つ目の箱は捨て、それを端に持つフローも捨てる
      (参照切れのフローを残すと、構造の検証で保存も表示もできなくなるため)。
    """
    by_id = {f.id: f for f in functions}
    processes: list[GeneratedProcess] = []
    for p in output.processes:
        function = by_id.get(p.function_id.strip())
        if function is None or any(q.id == function.id for q in processes):
            continue
        processes.append(
            GeneratedProcess(
                id=function.id,
                name=f"{function.id} {function.name}",
                description=p.description,
                layer=p.layer,
            )
        )
    node_ids = {p.id for p in processes}
    node_ids |= {n.id for n in (*output.external_entities, *output.data_stores)}
    flows = [f for f in output.flows if f.source_id in node_ids and f.target_id in node_ids]
    return DfdGenerationOutput(
        data_items=output.data_items,
        processes=processes,
        external_entities=output.external_entities,
        data_stores=output.data_stores,
        flows=flows,
    )
