"""段階5(主要処理の手順)のAIの下書きの入出力(純粋関数。docs/external_design.md 2.7節)。

1つの処理の手順を、LLM 1回で下書きする(処理ごとに生成・作り直す)。入力は、
その処理の機能一覧の行・処理概要表の行・DFD の R/W・CRUD 図の行と、ER のテーブル名・段階4の
モジュール一覧。

- 呼び出し先は、モジュール一覧のパスをそのまま書かせる(関与表の列の鍵)。少し違う書き方は
  `merge_procedure`(`resolve_callee`)がそろえ、そろわないものは検証のエラーで人に直させる。
- 手順番号は書かせない(並び順と分岐の印からシステムが振る。段階1の処理IDと同じ考え方)。
- 06(処理ロジックの詳細)との紐づけは書かせない。段階6が(呼び出し先, 関数)の一致から導く。
"""

from collections.abc import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.data_flow import ProcessSummaryRow
from app.detailed_design.data_model import CrudCell, DfdAccess
from app.detailed_design.function_list import FunctionRow
from app.detailed_design.procedure import ProcedureDraft, ProcedureStep
from app.detailed_design.prompt_rules import NAMING_RULES
from app.detailed_design.structure import ModuleRow


class GeneratedStep(BaseModel):
    """AIが下書きする手順の表の1行。"""

    caller: str = Field(
        description="呼び出し元。モジュール一覧のパス、または外部の役者(利用者・スケジューラなど)。"
        "分岐の行は空"
    )
    callee: str = Field(
        description="呼び出し先。【モジュール一覧】のパスをそのまま書く。"
        "利用者へ返す手順は外部の役者の名前(利用者など)。分岐の行は空"
    )
    call: str = Field(
        description="呼び出し先で呼ぶ関数・メソッド(例: ReservationService.create)。"
        "外部の役者や、同じモジュールの中の処理なら空"
    )
    data: str = Field(description="渡すデータ")
    action: str = Field(description="処理内容。分岐の行は分岐する条件")
    result: str = Field(description="結果(返す値)")
    db: str = Field(description="DB 操作(例: reservations R)。無ければ —")
    branch: str = Field(description="分岐・例外。分岐の行はその結果(例: NotFoundError → 404)")
    is_branch: bool = Field(description="分岐の行なら true(元の手順の直後に置く)")


class ProcedureGenerationOutput(BaseModel):
    """手順1つ分の構造化出力。"""

    reason: str = Field(description="この処理の手順を書く理由(何が難しい・重要か)を1文で")
    note: str = Field(description="トランザクションの範囲などの注記。無ければ空")
    steps: list[GeneratedStep]


PROCEDURE_SYSTEM_PROMPT = (
    "あなたは詳細設計の担当者です。【対象の処理】1つについて、実装の順に番号付きの手順の表を"
    "作成します。列は 呼び出し元 → 呼び出し先 / 渡すデータ / 処理内容 / 結果 / DB 操作 / "
    "分岐・例外 です。\n"
    "規則:\n"
    "- 呼び出し先(callee)は【モジュール一覧】のパスを一字一句そのまま書く。"
    "一覧に無いモジュールを創作しない。利用者・スケジューラ・外部サービスなどの外部の役者は、"
    "その名前を書く\n"
    "- 呼び出し元(caller)も同じ書き方にする。最初の手順の呼び出し元は、処理のトリガー"
    "(利用者・スケジューラなど)にする\n"
    "- call には呼び出し先で呼ぶ関数・メソッドの名前を書く。同じ関数は同じ名前で書く\n"
    "- 分岐・例外は、元の手順の直後に is_branch=true の行として置き、元の手順の branch 列には"
    "『1a へ』のように書く。分岐の行は action に条件、branch に結果を書き、caller・callee・call は"
    "空にする\n"
    "- 手順番号は書かない(システムが振る)。分岐の行の番号は、元の手順の番号に a, b を付けたもの"
    "になる\n"
    "- DB 操作は『テーブル名 C/R/U/D』の形で書き、【CRUD図】【DFDの読み書き】と食い違わない"
    "ようにする\n"
    "- 1つの処理の手順は、分岐を除いて 3〜12 行に収める。設定の読み込みのような定型の手順は省く\n"
    "- note にはトランザクションの範囲(『手順 3〜6 が1つのトランザクション』)など、"
    "表に書けない約束を書く\n"
    "- 処理ID・テーブル名・パスは入力の値をそのまま使い、入力に無い仕様を創作しない" + NAMING_RULES
)


def _module_lines(modules: Sequence[ModuleRow]) -> str:
    lines = []
    for m in modules:
        functions = "全処理" if m.all_functions else (", ".join(m.functions) or "—")
        depends = ", ".join(m.depends_on) or "—"
        lines.append(
            f"- {m.path}(層: {m.layer} / 責務: {m.responsibility} / "
            f"依存先: {depends} / 関わる処理: {functions})"
        )
    return "\n".join(lines)


def build_procedure_messages(
    function: FunctionRow,
    summary: ProcessSummaryRow | None,
    accesses: Sequence[DfdAccess],
    cells: Sequence[CrudCell],
    tables: Sequence[str],
    modules: Sequence[ModuleRow],
) -> list[BaseMessage]:
    """1つの処理の手順の下書きの入力。DFD の R/W と CRUD 図は、その処理の分だけを渡す。"""
    target = (
        f"- {function.id} {function.name}(種別: {function.kind} / トリガー: {function.trigger} / "
        f"機能グループ: {function.group})\n  概要: {function.summary or '—'}"
    )
    summary_text = (
        f"入力={summary.input} / 処理={summary.process} / 出力={summary.output}"
        if summary is not None
        else "(ありません)"
    )
    access_text = "\n".join(
        f"- {a.table}: {'読み' if a.kind == 'read' else '書き込み'}"
        for a in accesses
        if a.function_id == function.id
    )
    crud_text = "\n".join(f"- {c.table}: {c.ops}" for c in cells if c.function_id == function.id)
    content = "\n\n".join(
        [
            f"## 対象の処理\n{target}",
            f"## 処理概要表\n{summary_text}",
            f"## DFDの読み書き\n{access_text or '(ありません)'}",
            f"## CRUD図\n{crud_text or '(ありません)'}",
            "## テーブル\n" + ("\n".join(f"- {t}" for t in tables) or "(ありません)"),
            f"## モジュール一覧\n{_module_lines(modules) or '(ありません)'}",
        ]
    )
    return [SystemMessage(content=PROCEDURE_SYSTEM_PROMPT), HumanMessage(content=content)]


def to_procedure_draft(output: ProcedureGenerationOutput) -> ProcedureDraft:
    """構造化出力を、merge_procedure の入力に変える。"""
    return ProcedureDraft(
        reason=output.reason,
        note=output.note,
        steps=tuple(ProcedureStep(**step.model_dump()) for step in output.steps),
    )
