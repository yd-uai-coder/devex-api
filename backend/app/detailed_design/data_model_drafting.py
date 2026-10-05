"""段階3(データモデル)のAIの下書きの入出力(純粋関数。docs/external_design.md 2.7節)。

1回の生成で、次の2種類を順に呼ぶ:

- ER(テーブル定義を含む): 入力は、段階2の DFD のデータストア名・データ辞書・処理概要表(LLM 1回)。
- CRUD 図: 入力は、機能一覧・処理概要表・先に作った ER のテーブル名・DFD の R/W(LLM 1回)。

ER の出力スキーマは段階3専用にし、`GeneratedColumn`に制約・説明を足す(テーブル
定義の正本を ER に置くため)。

CRUD 図の DFD から決まる部分(R と、書き込みがあること)は、AI に「決まっているもの」として渡す。
AI に決めさせるのは、書き込みの C/U/D の区別と、DFD を描いていない処理の分だけ。AI が決まった
部分を書き漏らしても、`merge_crud`が DFD から足し直す。
"""

from collections.abc import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.data_flow import ProcessSummaryRow
from app.detailed_design.data_model import CrudDraft, DfdAccess
from app.detailed_design.function_list import FunctionRow
from app.detailed_design.prompt_rules import NAMING_RULES
from app.uml.domain.er import ErColumn, ErElement, ErRelation, ErSemanticModel
from app.uml.generation.prompts import ExistingDataItem
from app.uml.generation.schemas import GeneratedColumn, GeneratedTableRelation
from app.uml.validation.structural import MAX_ELEMENTS


class DraftedColumn(GeneratedColumn):
    """段階3の ER の列。`GeneratedColumn`に、テーブル定義の制約・説明を足す。"""

    constraints: str = Field(
        default="",
        description="制約(NOT NULL 以外。UNIQUE・既定値・INDEX・FK の参照先と削除時の動き)。"
        "無ければ空",
    )
    description: str = Field(default="", description="列の意味を短く")


class DraftedTable(BaseModel):
    """段階3の ER のテーブル1つ。"""

    id: str = Field(description="図の中で一意なID(例: t1)")
    name: str = Field(
        description="テーブル名(英小文字の複数形。【データストア】にある名前はそのまま使う)"
    )
    description: str = Field(default="", description="テーブルの役割・複合一意制約などを短く")
    columns: list[DraftedColumn]


class DataModelErOutput(BaseModel):
    """段階3の ER の構造化出力。"""

    tables: list[DraftedTable]
    relations: list[GeneratedTableRelation]


class GeneratedCrudCell(BaseModel):
    """AIが下書きする CRUD 図のセル1つ。"""

    function_id: str = Field(description="【機能一覧】の処理ID(F-01など)をそのまま書く")
    table: str = Field(description="【テーブル】のテーブル名をそのまま書く")
    ops: str = Field(description="操作。C(作成)・R(読み)・U(更新)・D(削除)の組み合わせ(例: CR)")


class CrudGenerationOutput(BaseModel):
    """CRUD 図の構造化出力。操作の無いセルは含めない。"""

    cells: list[GeneratedCrudCell]


ER_SYSTEM_PROMPT = (
    "あなたはデータベース設計の担当者です。【データストア】【データ辞書】【処理概要表】から、"
    "ER図(テーブル定義を含む)の意味モデルを作成します。\n"
    "規則:\n"
    "- 【データストア】の名前は、そのままテーブル名に使い、全てのデータストアをテーブルにする\n"
    "- 処理に必要なら、データストアに無いテーブル(多対多の中間テーブルなど)を足してよい\n"
    "- 各テーブルに主キーの列を1つ置く。列は【データ辞書】のフィールドを手がかりにする\n"
    "- constraints には NOT NULL 以外の制約(UNIQUE・既定値・INDEX・FK の参照先と削除時の動き)を、"
    "description には列の意味を短く書く。複合一意制約はテーブルの description に書く\n"
    "- relations の source_id は参照される側(1側)、target_id は外部キーを持つ側のテーブルID\n"
    f"- テーブルは合計{MAX_ELEMENTS}個以内に収める\n"
    "- 入力に書かれていない業務のテーブルを創作しない"
    + NAMING_RULES
)

CRUD_SYSTEM_PROMPT = (
    "あなたは詳細設計の担当者です。【機能一覧】の処理ごとに、【テーブル】の各テーブルへの操作"
    "(C 作成・R 読み・U 更新・D 削除)を CRUD 図のセルとして書きます。\n"
    "規則:\n"
    "- 【DFD から決まっている読み書き】は必ず含める。「読み」のセルには R を、"
    "「書き込み」のセルには C・U・D のどれか(複数可)を書く\n"
    "- DFD に無い処理も、【処理概要表】から操作を判断して書く\n"
    "- 操作の無いセルは書かない。function_id と table は入力の値をそのまま書く\n"
    "- 処理概要表から読み取れない操作を創作しない"
    + NAMING_RULES
)


def _function_lines(functions: Sequence[FunctionRow]) -> str:
    return "\n".join(f"- {f.id} {f.name}(機能グループ: {f.group}){f.summary}" for f in functions)


def _summary_lines(summaries: Sequence[ProcessSummaryRow]) -> str:
    return "\n".join(
        f"- {s.function_id}: 入力={s.input} / 処理={s.process} / 出力={s.output}"
        for s in summaries
    )


def data_store_names(accesses: Sequence[DfdAccess]) -> list[str]:
    """DFD の R/W に現れるデータストア名(正規化済み)を、重複を除いて並べる。"""
    return sorted({access.table for access in accesses})


def build_er_messages(
    stores: Sequence[str],
    data_items: Sequence[ExistingDataItem],
    summaries: Sequence[ProcessSummaryRow],
) -> list[BaseMessage]:
    """ER の下書きの入力。DFD のデータストア名・データ辞書・処理概要表を渡す。"""
    dictionary = "\n".join(f"- {item.name}({', '.join(item.field_names)})" for item in data_items)
    content = "\n\n".join(
        [
            f"## データストア\n{chr(10).join(f'- {s}' for s in stores) or '(ありません)'}",
            f"## データ辞書\n{dictionary or '(ありません)'}",
            f"## 処理概要表\n{_summary_lines(summaries) or '(ありません)'}",
        ]
    )
    return [SystemMessage(content=ER_SYSTEM_PROMPT), HumanMessage(content=content)]


def to_er_model(output: DataModelErOutput) -> ErSemanticModel:
    """ER の出力を意味モデルにする。同じ ID・同じ名前の2つ目のテーブルは捨て、端のテーブルが
    無い関連も捨てる(参照切れの関連を残すと、構造の検証で保存も表示もできなくなるため)。"""
    elements: list[ErElement] = []
    for table in output.tables:
        name = table.name.strip()
        if any(e.id == table.id or e.name == name for e in elements):
            continue
        elements.append(
            ErElement(
                id=table.id,
                name=name,
                description=table.description,
                columns=[ErColumn(**column.model_dump()) for column in table.columns],
            )
        )
    ids = {e.id for e in elements}
    relations = [
        ErRelation(
            id=r.id, source_id=r.source_id, target_id=r.target_id, relation_type=r.relation_type
        )
        for r in output.relations
        if r.source_id in ids and r.target_id in ids
    ]
    return ErSemanticModel(elements=elements, relations=relations)


def build_crud_messages(
    functions: Sequence[FunctionRow],
    summaries: Sequence[ProcessSummaryRow],
    tables: Sequence[str],
    accesses: Sequence[DfdAccess],
) -> list[BaseMessage]:
    """CRUD 図の下書きの入力。機能一覧・処理概要表・ER のテーブル名・DFD の読み書きを渡す。"""
    kinds = {"read": "読み", "write": "書き込み"}
    fixed = "\n".join(f"- {a.function_id} × {a.table}: {kinds[a.kind]}" for a in accesses)
    content = "\n\n".join(
        [
            f"## 機能一覧\n{_function_lines(functions)}",
            f"## 処理概要表\n{_summary_lines(summaries) or '(ありません)'}",
            f"## テーブル\n{chr(10).join(f'- {t}' for t in tables) or '(ありません)'}",
            f"## DFD から決まっている読み書き\n{fixed or '(ありません)'}",
        ]
    )
    return [SystemMessage(content=CRUD_SYSTEM_PROMPT), HumanMessage(content=content)]


def to_crud_drafts(output: CrudGenerationOutput) -> list[CrudDraft]:
    """構造化出力を、merge_crud の入力に変える。"""
    return [CrudDraft(function_id=c.function_id, table=c.table, ops=c.ops) for c in output.cells]
