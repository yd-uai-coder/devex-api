"""段階2 データフローの意味モデルと、下書きの組み立て(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」。

`design_stages.model`(段階2)に持つのは、人が選んだ「DFD を描く機能グループ」と、全処理の
処理概要表(入力/処理内容/出力)だけにする。DFD 本体は`uml_diagrams`(notation=dfd、
subject=機能グループ名)、データ辞書は`data_items`が正本で、ここには複製しない(二重に持つと、
DFD のエディタで直した内容と食い違うため)。

処理概要表の行は、段階1の処理IDで処理を指す。DFD を描かない単純な処理も、ここの1行で済ませる
(docs/external_design.md 2.7節)。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.detailed_design.function_list import FunctionListModel, FunctionRow

# データフローの段階の番号(DFD・データ辞書を直したときに差し戻す段階。app/services から参照する)
DATA_FLOW_STAGE = 2

# 1回の生成で DFD を描けるグループの数の上限(UML図の生成の1回5件と同じ。1グループで LLM を
# 1回呼ぶので、15分の回収のしきい値に収めるため)
MAX_DFD_GROUPS = 5

# DFD の承認済みとみなす図の状態(出力済みも承認を経ている)
APPROVED_DIAGRAM_STATUSES = frozenset({"approved", "exported"})


class ProcessSummaryRow(BaseModel):
    """処理概要表の1行。`function_id`は段階1の処理ID。"""

    function_id: str
    input: str = ""
    process: str = ""
    output: str = ""


class DataFlowModel(BaseModel):
    """段階2の意味モデル。`dfd_groups`は DFD を描く機能グループ(人が選ぶ。表示順)。"""

    dfd_groups: list[str] = Field(default_factory=list)
    summaries: list[ProcessSummaryRow] = Field(default_factory=list)


@dataclass(frozen=True)
class ProcessSummaryDraft:
    """AIの下書きの処理概要表1行。"""

    function_id: str
    input: str
    process: str
    output: str


def dfd_subject(group: str) -> str:
    """機能グループの DFD を`uml_diagrams`で識別するキー(subject)。機能グループ名そのもの。

    詳細設計モードのプロジェクトには内部設計書が無く、簡易ドキュメントモードの DFD(subject は
    処理名)と同じプロジェクトに並ぶことは無いため、接頭辞は付けない。"""
    return group.strip()


def group_functions(function_list: FunctionListModel, group: str) -> list[FunctionRow]:
    """機能グループに属する処理を、機能一覧の並びのまま返す。"""
    return [row for row in function_list.functions if row.group == group]


def merge_summaries(
    drafts: Sequence[ProcessSummaryDraft],
    function_list: FunctionListModel,
    previous: DataFlowModel | None = None,
) -> DataFlowModel:
    """AIの下書きを、段階1の機能一覧の処理ごとに1行の処理概要表にする。

    - 行の並びは機能一覧の並び。機能一覧に無い処理IDの下書きは捨てる。
    - 同じ処理IDの下書きが2行あれば、最初の行を使う。
    - 下書きに無い処理は、前の版の行(人が書いた内容)を残し、それも無ければ空の行にする。
    - `dfd_groups`は人の選択なので前の版から引き継ぐ(機能一覧から消えたグループは落とす)。
    """
    previous = previous or DataFlowModel()
    drafted: dict[str, ProcessSummaryDraft] = {}
    for draft in drafts:
        drafted.setdefault(draft.function_id.strip(), draft)
    kept = {row.function_id: row for row in previous.summaries}

    summaries: list[ProcessSummaryRow] = []
    for function in function_list.functions:
        draft = drafted.get(function.id)
        if draft is not None:
            summaries.append(
                ProcessSummaryRow(
                    function_id=function.id,
                    input=draft.input.strip(),
                    process=draft.process.strip(),
                    output=draft.output.strip(),
                )
            )
        else:
            summaries.append(kept.get(function.id) or ProcessSummaryRow(function_id=function.id))

    groups = set(function_list.groups)
    dfd_groups = [g for g in dict.fromkeys(previous.dfd_groups) if g in groups]
    return DataFlowModel(dfd_groups=dfd_groups, summaries=summaries)
