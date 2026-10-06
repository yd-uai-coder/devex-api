"""段階6(処理ロジックの詳細)のAIの下書きの入出力(純粋関数。docs/external_design.md 2.7節)。

1つの関数の詳細を、LLM 1回で下書きする(関数ごとに生成・作り直す。段階5と同じ形)。入力は、
その関数のモジュール(段階4のモジュール一覧の行)、その関数を呼ぶ段階5の手順(直後の分岐の行を
含む)と、ER のテーブル名。

- モジュールと関数の名前は書かせない(人が選んだ (モジュール, 関数) が 05 との紐づけの鍵のため)。
- L-ID は書かせない(並び順からシステムが振る)。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.function_list import FunctionRow
from app.detailed_design.logic import LogicDraft, PseudoStep, logic_key
from app.detailed_design.procedure import ProcedureModel, ProcedureStep, number_steps, step_id
from app.detailed_design.prompt_rules import NAMING_RULES
from app.detailed_design.structure import ModuleRow


class GeneratedPseudoStep(BaseModel):
    """AIが下書きする擬似フローの1段。"""

    text: str = Field(description="この段で行うこと(1文)")
    sub: list[str] = Field(description="条件の分かれ目・細かい手順の箇条。無ければ空")


class LogicGenerationOutput(BaseModel):
    """関数1つ分の構造化出力。"""

    signature: str = Field(description="シグネチャ(技術スタックの言語で、型を含めて1行)")
    args: str = Field(description="引数ごとの意味(『名前: 意味』を / で区切る)")
    returns: str = Field(description="戻り値の意味")
    raises: str = Field(description="送出する例外と、その対応(HTTP の状態など)。無ければ なし")
    pre: str = Field(description="事前条件(呼び出し元が満たしておくこと)")
    post: str = Field(description="事後条件(成功したときに成り立つこと)")
    pseudo: list[GeneratedPseudoStep] = Field(description="番号付きの擬似フロー(3〜10段)")


@dataclass(frozen=True)
class CallingStep:
    """関数を呼ぶ段階5の手順1つ。`branches`は直後の分岐の行(条件と結果)。"""

    step_id: str
    function_id: str
    step: ProcedureStep
    branches: tuple[ProcedureStep, ...]


def calling_step_rows(procedures: ProcedureModel, module: str, function: str) -> list[CallingStep]:
    """(モジュール, 関数)を呼ぶ手順を、直後の分岐の行と一緒に集める(段階5の並び順)。"""
    key = logic_key(module, function)
    rows: list[CallingStep] = []
    for procedure in procedures.procedures:
        steps = procedure.steps
        numbers = number_steps(steps)
        for index, step in enumerate(steps):
            if step.is_branch or logic_key(step.callee, step.call) != key:
                continue
            branches: list[ProcedureStep] = []
            for following in steps[index + 1 :]:
                if not following.is_branch:
                    break
                branches.append(following)
            rows.append(
                CallingStep(
                    step_id=step_id(procedure.function_id, numbers[index]),
                    function_id=procedure.function_id,
                    step=step,
                    branches=tuple(branches),
                )
            )
    return rows


LOGIC_SYSTEM_PROMPT = (
    "あなたは詳細設計の担当者です。【対象の関数】1つについて、モジュール仕様(シグネチャ/引数/"
    "戻り値/例外/事前条件/事後条件)と番号付きの擬似フローを作成します。\n"
    "規則:\n"
    "- 【呼ばれる手順】のすべての呼び出し方(渡すデータ・結果・分岐)を満たす仕様にする。"
    "手順の分岐・例外は、例外の欄と擬似フローの分かれ目に必ず現れるようにする\n"
    "- シグネチャはモジュールのパスから分かる言語(.py なら Python、.ts なら TypeScript)で、"
    "型を含めて1行で書く。関数名は【対象の関数】の名前をそのまま使う\n"
    "- 事前条件には、呼び出し元が満たしておくこと(ロック・トランザクション・認証など)を書く\n"
    "- 擬似フローは実装の順に 3〜10 段で書く。条件の分かれ目や細かい手順は sub に箇条で書く。"
    "コードそのものは書かない\n"
    "- テーブル名・パス・関数名は入力の値をそのまま使い、入力に無い仕様を創作しない" + NAMING_RULES
)


def _step_line(row: CallingStep, functions: Mapping[str, FunctionRow]) -> str:
    function = functions.get(row.function_id)
    name = function.name if function is not None else "(機能一覧にありません)"
    s = row.step
    line = (
        f"- {row.step_id}({row.function_id} {name}): 呼び出し元={s.caller or '—'} / "
        f"渡すデータ={s.data or '—'} / 処理内容={s.action or '—'} / 結果={s.result or '—'} / "
        f"DB 操作={s.db or '—'}"
    )
    branches = [f"  - 分岐: {b.action or '—'} → {b.branch or '—'}" for b in row.branches]
    return "\n".join([line, *branches])


def build_logic_messages(
    module: str,
    function: str,
    module_row: ModuleRow | None,
    rows: Sequence[CallingStep],
    functions: Mapping[str, FunctionRow],
    tables: Sequence[str],
) -> list[BaseMessage]:
    """1つの関数の詳細の下書きの入力。"""
    if module_row is not None:
        depends = ", ".join(module_row.depends_on) or "—"
        module_text = (
            f"層: {module_row.layer} / 責務: {module_row.responsibility or '—'} / 依存先: {depends}"
        )
    else:
        module_text = "(モジュール一覧にありません)"
    steps_text = "\n".join(_step_line(row, functions) for row in rows)
    content = "\n\n".join(
        [
            f"## 対象の関数\n- モジュール: {module}\n- 関数: {function}",
            f"## モジュール\n{module_text}",
            f"## 呼ばれる手順\n{steps_text or '(ありません)'}",
            "## テーブル\n" + ("\n".join(f"- {t}" for t in tables) or "(ありません)"),
        ]
    )
    return [SystemMessage(content=LOGIC_SYSTEM_PROMPT), HumanMessage(content=content)]


def to_logic_draft(output: LogicGenerationOutput) -> LogicDraft:
    """構造化出力を、merge_logic の入力に変える。"""
    return LogicDraft(
        signature=output.signature,
        args=output.args,
        returns=output.returns,
        raises=output.raises,
        pre=output.pre,
        post=output.post,
        pseudo=tuple(PseudoStep(text=p.text, sub=list(p.sub)) for p in output.pseudo),
    )
