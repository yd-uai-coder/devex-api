"""段階6 処理ロジックの詳細の意味モデルと、05(手順)との紐づけ(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」。

段階6は任意の段階で、人が選んだ関数ごとに、シグネチャ/引数/戻り値/例外/事前条件/事後条件と
番号付きの擬似フローを持つ。`design_stages.model`(段階6)に持つ。

- 選んだ関数 = `logics`の行。シグネチャと擬似フローが両方空の行は、まだ下書きを作っていない関数。
- 関数の候補は、承認済みの段階5の手順のうち、呼び出し先がモジュール(パス)で、呼ぶ関数(`call`)が
  空でない行。同じ(モジュール, 関数)を呼ぶ手順は1つの候補にまとまる(`logic_candidates`)。
- 05↔06 の紐づけは保存しない。手順の(呼び出し先`callee`, 関数`call`)と、項目の(モジュール, 関数)の
  一致から導く(Phase 20 の決定)。段階6で関数を選んでも、承認済みの段階5は書き換わらない。
- L-ID(`L-01`…)は保存しない。並び順から`logic_id`で導く(段階5の手順番号と同じ考え方)。紐づけは
  (モジュール, 関数)なので、行の並べ替えで L-ID が振り直されても切れない。
- 0件で承認する = 段階6を飛ばす(06章は「省略」になる。Phase 21 の決定)。
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.detailed_design.procedure import (
    ProcedureModel,
    is_external_actor,
    number_steps,
    step_id,
)

# 処理ロジックの詳細の段階の番号
LOGIC_STAGE = 6

# 1回の生成で下書きを作る関数の数の上限(1関数 LLM 1回。段階5の処理の上限と同じ値)
MAX_LOGIC_TARGETS = 5


class PseudoStep(BaseModel):
    """擬似フローの1段。`sub`はその段の下位の箇条(条件の分かれ目・細かい手順)。"""

    text: str = ""
    sub: list[str] = Field(default_factory=list)


class LogicRow(BaseModel):
    """06 の1項目(関数1つ)。`module`は段階4のモジュール一覧のパス、`function`は手順の`call`。"""

    module: str
    function: str
    signature: str = ""
    args: str = ""
    returns: str = ""
    raises: str = ""
    pre: str = ""
    post: str = ""
    pseudo: list[PseudoStep] = Field(default_factory=list)


class LogicModel(BaseModel):
    """段階6の意味モデル。"""

    logics: list[LogicRow] = Field(default_factory=list)


@dataclass(frozen=True)
class LogicDraft:
    """AIの下書きの、関数1つ分の仕様。"""

    signature: str
    args: str
    returns: str
    raises: str
    pre: str
    post: str
    pseudo: tuple[PseudoStep, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LogicCandidate:
    """段階6で選べる関数1つ。`step_ids`はその関数を呼ぶ手順の手順ID(段階5の並び順)。"""

    module: str
    function: str
    step_ids: tuple[str, ...]


def logic_id(index: int) -> str:
    """並び順(0始まり)から L-ID を導く(`L-01`…。100件目からは3桁)。"""
    return f"L-{index + 1:02d}"


def logic_key(module: str, function: str) -> str:
    """(モジュール, 関数)の組を1つの文字列にした鍵(生成の対象の受け渡しと重複の判定に使う)。
    パスにも関数名にも現れない「::」でつなぐ。"""
    return f"{module.strip()}::{function.strip()}"


def is_drafted(row: LogicRow) -> bool:
    """下書き(または人の記入)がある関数か。シグネチャか擬似フローのどちらかがあれば、ある。"""
    return bool(row.signature.strip() or row.pseudo)


def logic_candidates(procedures: ProcedureModel) -> list[LogicCandidate]:
    """段階5の手順から、段階6で選べる関数を集める(最初に現れた順)。

    対象は、分岐でなく、呼び出し先がモジュール(「/」を含むパス)で、呼ぶ関数が空でない行。"""
    found: dict[str, tuple[str, str, list[str]]] = {}
    for procedure in procedures.procedures:
        numbers = number_steps(procedure.steps)
        for step, number in zip(procedure.steps, numbers, strict=True):
            callee, call = step.callee.strip(), step.call.strip()
            if step.is_branch or not callee or not call or is_external_actor(callee):
                continue
            key = logic_key(callee, call)
            entry = found.setdefault(key, (callee, call, []))
            entry[2].append(step_id(procedure.function_id, number))
    return [
        LogicCandidate(module=module, function=function, step_ids=tuple(ids))
        for module, function, ids in found.values()
    ]


def calling_steps(procedures: ProcedureModel, module: str, function: str) -> list[str]:
    """(モジュール, 関数)を呼ぶ手順の手順ID(06 の「呼ばれる手順」)。"""
    key = logic_key(module, function)
    for candidate in logic_candidates(procedures):
        if logic_key(candidate.module, candidate.function) == key:
            return list(candidate.step_ids)
    return []


def _normalize_pseudo(pseudo: Sequence[PseudoStep]) -> list[PseudoStep]:
    """擬似フローを整える。前後の空白を除き、空の箇条と、本文も箇条も空の段を捨てる。"""
    rows: list[PseudoStep] = []
    for step in pseudo:
        text = step.text.strip()
        sub = [item.strip() for item in step.sub if item.strip()]
        if text or sub:
            rows.append(PseudoStep(text=text, sub=sub))
    return rows


def merge_logic(model: LogicModel, key: str, draft: LogicDraft) -> LogicModel:
    """1つの関数の仕様を、AIの下書きで置き換える(他の関数の手直しはそのまま残す)。
    対象(`logic_key`が`key`の行)が無ければ何もしない(生成の受け付けで、選んだ関数だけに絞っている)。"""
    logics: list[LogicRow] = []
    for row in model.logics:
        if logic_key(row.module, row.function) != key:
            logics.append(row)
            continue
        logics.append(
            LogicRow(
                module=row.module.strip(),
                function=row.function.strip(),
                signature=draft.signature.strip(),
                args=draft.args.strip(),
                returns=draft.returns.strip(),
                raises=draft.raises.strip(),
                pre=draft.pre.strip(),
                post=draft.post.strip(),
                pseudo=_normalize_pseudo(draft.pseudo),
            )
        )
    return LogicModel(logics=logics)


def pending_logic_keys(model: LogicModel) -> list[str]:
    """まだ下書きの無い関数の鍵(選んだ順)。"""
    return [logic_key(row.module, row.function) for row in model.logics if not is_drafted(row)]


def generation_targets(model: LogicModel, requested: Sequence[tuple[str, str]] | None) -> list[str]:
    """下書きを作る関数の鍵。指定が無ければ、まだ下書きの無い関数(`pending_logic_keys`)。
    指定があれば、重複を除いてその順に使う(タブごとの作り直し)。"""
    if requested is None:
        return pending_logic_keys(model)
    keys = [logic_key(module, function) for module, function in requested]
    return list(dict.fromkeys(key for key in keys if key != "::"))
