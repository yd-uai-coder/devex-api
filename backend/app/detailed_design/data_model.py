"""段階3 データモデルの意味モデルと、CRUD 図の組み立て(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」。

`design_stages.model`(段階3)に持つのは CRUD 図のセルだけにする。ER とテーブル定義(列の制約・
説明)は`uml_diagrams`(notation=er、subject='')が正本で、ここには複製しない(段階2の DFD と
同じ考え方。二重に持つと、ER のエディタで直した内容と食い違うため)。

CRUD 図の R(読み)と W(書き)は、段階2の DFD の線の向きから決定的に作る:

- データストア → 処理 の線 = その処理がそのテーブルを読む(R)。
- 処理 → データストア の線 = その処理がそのテーブルに書く(C/U/D のどれか)。

DFD の処理の箱の id は段階1の処理ID、データストアの名前はテーブル名(英小文字の複数形)なので、
名前で ER のテーブルと突き合わせる。W の C/U/D の区別と、DFD を描いていない処理の分は AI が
下書きし、そのセルには`draft`の印を付ける。人が直したセルは印が外れ、段階3の承認で残りの印も
外す(承認 = 人の確定)。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.detailed_design.function_list import FunctionListModel
from app.uml.domain.er import ErSemanticModel

# データモデルの段階の番号(ER を直したときに差し戻す段階。app/services から参照する)
DATA_MODEL_STAGE = 3

# 段階3の ER を`uml_diagrams`で識別するキー(subject)。全体1枚なので空文字列
# (部分図の ER は無い)
ER_SUBJECT = ""

# CRUD 図のセルに書ける操作。セルの文字列はこの順に並べる(「RC」ではなく「CR」)
CRUD_OPS = "CRUD"
WRITE_OPS = frozenset("CUD")

AccessKind = Literal["read", "write"]


@dataclass(frozen=True, order=True)
class DfdAccess:
    """DFD の線1本から読み取れる、処理とテーブルの関わり。`table`は`table_key`で正規化した名前。"""

    function_id: str
    table: str
    kind: AccessKind


class CrudCell(BaseModel):
    """CRUD 図のセル1つ(処理 × テーブル)。`draft`は AI の下書きのまま人が確定していない印。"""

    function_id: str
    table: str
    ops: str = ""
    draft: bool = False


class CrudModel(BaseModel):
    """段階3の意味モデル。空のセル(操作なし)は持たない。"""

    cells: list[CrudCell] = Field(default_factory=list)


@dataclass(frozen=True)
class CrudDraft:
    """AIの下書きの CRUD 図のセル1つ。"""

    function_id: str
    table: str
    ops: str


def table_key(name: str) -> str:
    """データストアとテーブルを突き合わせるための名前(前後の空白を除き、小文字にする)。"""
    return name.strip().lower()


def normalize_ops(ops: str) -> str:
    """操作の文字列を`CRUD_OPS`の順に並べ直す(大文字にし、C/R/U/D 以外の文字は捨てる)。"""
    letters = set(ops.upper())
    return "".join(op for op in CRUD_OPS if op in letters)


def is_canonical_ops(ops: str) -> bool:
    """`ops`が並べ直しの済んだ形(空でない・C/R/U/D だけ・重複なし・この順)か。"""
    return bool(ops) and normalize_ops(ops) == ops


def er_table_names(semantic_model: Mapping[str, Any] | None) -> list[str]:
    """ER の意味モデルのテーブル名を、図の要素の並びのまま返す。"""
    model = ErSemanticModel.model_validate(semantic_model or {})
    return [element.name for element in model.elements]


def tables_without_primary_key(semantic_model: Mapping[str, Any] | None) -> list[str]:
    """主キーの列が1つも無いテーブルの名前。"""
    model = ErSemanticModel.model_validate(semantic_model or {})
    return [
        element.name
        for element in model.elements
        if not any(column.is_primary_key for column in element.columns)
    ]


def dfd_accesses(semantic_models: Iterable[Mapping[str, Any]]) -> list[DfdAccess]:
    """DFD の意味モデル(複数)の線から、処理とテーブルの関わりを読み取る(重複を除き、並べる)。

    処理とデータストアを結ぶ線だけを見る(外部実体との線、処理どうしの線は CRUD に関係しない)。"""
    accesses: set[DfdAccess] = set()
    for model in semantic_models:
        elements = {str(e.get("id")): e for e in model.get("elements", [])}
        for relation in model.get("relations", []):
            source = elements.get(str(relation.get("source_id")))
            target = elements.get(str(relation.get("target_id")))
            if source is None or target is None:
                continue
            kinds = (source.get("element_type"), target.get("element_type"))
            if kinds == ("data_store", "process"):
                table = table_key(str(source.get("name", "")))
                accesses.add(DfdAccess(str(target.get("id")), table, "read"))
            elif kinds == ("process", "data_store"):
                table = table_key(str(target.get("name", "")))
                accesses.add(DfdAccess(str(source.get("id")), table, "write"))
    return sorted(accesses)


def merge_crud(
    drafts: Sequence[CrudDraft],
    accesses: Sequence[DfdAccess],
    function_list: FunctionListModel,
    tables: Sequence[str],
) -> CrudModel:
    """AIの下書きと DFD の R/W から、CRUD 図を組み立てる(再生成では前の版を使わず置き換える)。

    - 機能一覧に無い処理・ER に無いテーブルのセルは捨てる。行は機能一覧の並び、列は ER の並び。
    - DFD に読みの線があるセルには R を足す(DFD から決まる部分。AI の下書きによらない)。
    - DFD に書き込みの線があるセルは、AI の C/U/D が無くても残す(検証のエラーで人に決めさせる)。
    - `draft`: DFD の読みで決まる R 以外の操作がある、または DFD に書き込みがある(C/U/D の区別は
      AI か人が決める)セル。DFD の読みだけのセルは確定とする。
    """
    function_ids = [row.id for row in function_list.functions]
    table_names = {table_key(name): name for name in tables}
    ops: dict[tuple[str, str], set[str]] = {}
    for draft in drafts:
        key = (draft.function_id.strip(), table_key(draft.table))
        if key[0] in function_ids and key[1] in table_names:
            ops.setdefault(key, set()).update(normalize_ops(draft.ops))

    reads = {(a.function_id, a.table) for a in accesses if a.kind == "read"}
    writes = {(a.function_id, a.table) for a in accesses if a.kind == "write"}
    for key in reads | writes:
        if key[0] in function_ids and key[1] in table_names:
            ops.setdefault(key, set())
    for key in reads & ops.keys():
        ops[key].add("R")

    cells: list[CrudCell] = []
    for function_id in function_ids:
        for key_name, name in table_names.items():
            key = (function_id, key_name)
            if key not in ops:
                continue
            letters = ops[key]
            fixed = {"R"} if key in reads else set()
            if not letters and key not in writes:
                continue
            cells.append(
                CrudCell(
                    function_id=function_id,
                    table=name,
                    ops=normalize_ops("".join(letters)),
                    draft=bool(letters - fixed) or key in writes,
                )
            )
    return CrudModel(cells=cells)


def confirm_drafts(model: Mapping[str, Any]) -> dict:
    """段階3の承認で、残っている下書きの印をすべて外す(承認 = 人がまとめて確定したとみなす)。"""
    parsed = CrudModel.model_validate(model)
    for cell in parsed.cells:
        cell.draft = False
    return parsed.model_dump(mode="json")
