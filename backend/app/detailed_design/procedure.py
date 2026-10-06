"""段階5 主要処理の手順の意味モデルと、手順の組み立て(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」。

段階5は、人が選んだ処理ごとに、番号付きの手順の表(呼び出し元 → 呼び出し先 / 渡すデータ /
処理内容 / 結果 / DB 操作 / 分岐・例外)を持つ。`design_stages.model`(段階5)に持つ。

- 選んだ処理 = `procedures`の行。`steps`が空の行は、まだ下書きを作っていない処理。
- 手順番号は保存しない。並び順と`is_branch`から`number_steps`で導く(`1, 1a, 2…`)。行を足したり
  消したりしても、番号の付け直しを人や AI に任せずに済む。
- 呼び出し先(`callee`)は、段階4のモジュール一覧のパスか、外部の役者(利用者・スケジューラなど。
  「/」を含まない名前)。パスは「処理 × モジュール」の関与表の列の鍵なので、`resolve_callee`で
  モジュール一覧の行のパスにそろえ、そろわないものは検証のエラーにする。
- 06(処理ロジックの詳細、段階6)との紐づけは、手順の(呼び出し先, 呼ぶ関数`call`)と、段階6の
  項目の(モジュール, 関数)の一致から導く。手順の行に L-ID を持たせないので、
  段階6で関数を選んでも承認済みの段階5は書き換わらない。
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from string import ascii_lowercase

from pydantic import BaseModel, Field

from app.detailed_design.structure import module_ref_matches

# 主要処理の手順の段階の番号
PROCEDURE_STAGE = 5

# 1回の生成で下書きを作る処理の数の上限(1処理 LLM 1回。15分の回収のしきい値に収めるため。
# 段階2の DFD を描くグループの上限と同じ値)
MAX_PROCEDURE_TARGETS = 5


class ProcedureStep(BaseModel):
    """手順の表の1行。分岐の行(`is_branch`)は、`action`に条件、`branch`に結果を書き、
    呼び出し元・呼び出し先・関数は空にする。分岐の行は、元の手順の直後に置く。"""

    caller: str = ""
    callee: str = ""
    call: str = ""
    data: str = ""
    action: str = ""
    result: str = ""
    db: str = ""
    branch: str = ""
    is_branch: bool = False


class Procedure(BaseModel):
    """手順を書く処理1つ。`reason`は対象に選んだ理由、`note`はトランザクションの範囲などの注記。"""

    function_id: str
    reason: str = ""
    note: str = ""
    steps: list[ProcedureStep] = Field(default_factory=list)


class ProcedureModel(BaseModel):
    """段階5の意味モデル。"""

    procedures: list[Procedure] = Field(default_factory=list)


@dataclass(frozen=True)
class ProcedureDraft:
    """AIの下書きの、処理1つ分の手順。"""

    reason: str
    note: str
    steps: tuple[ProcedureStep, ...] = field(default_factory=tuple)


def _branch_suffix(index: int) -> str:
    """分岐の番号の添え字(1 → a、26 → z、27 → aa)。"""
    letters = ""
    while index > 0:
        index, rest = divmod(index - 1, len(ascii_lowercase))
        letters = ascii_lowercase[rest] + letters
    return letters


def number_steps(steps: Sequence[ProcedureStep]) -> list[str]:
    """手順番号を並び順から導く。分岐の行は直前の手順の番号に a, b… を付ける。
    先頭の分岐の行(元の手順が無い)は`0a`のようにする(検証のエラーで直させる)。"""
    numbers: list[str] = []
    main = 0
    sub = 0
    for step in steps:
        if step.is_branch:
            sub += 1
            numbers.append(f"{main}{_branch_suffix(sub)}")
        else:
            main += 1
            sub = 0
            numbers.append(str(main))
    return numbers


def step_id(function_id: str, number: str) -> str:
    """文書全体で一意な手順ID(`F-01#4`、分岐は`F-01#4a`)。"""
    return f"{function_id}#{number}"


def is_external_actor(callee: str) -> bool:
    """呼び出し先が外部の役者(利用者・スケジューラなど)か。モジュールはパスなので「/」を含む。"""
    return "/" not in callee


def resolve_callee(callee: str, module_paths: Sequence[str]) -> str:
    """AIの書いた呼び出し先を、モジュール一覧の行のパスにそろえる。

    パスと完全一致すればそのまま、区切り単位の部分一致(`module_ref_matches`)で1行だけに当たれば
    その行のパスに置き換える(`services/reservation` → `app/services/reservation.py`)。
    外部の役者・複数の行に当たる・どの行にも当たらないものは、そのまま残す(検証で人に直させる)。
    """
    text = callee.strip()
    if is_external_actor(text) or text in module_paths:
        return text
    matches = [path for path in module_paths if module_ref_matches(text, path)]
    return matches[0] if len(matches) == 1 else text


def _normalize_steps(
    steps: Sequence[ProcedureStep], module_paths: Sequence[str]
) -> list[ProcedureStep]:
    """下書きの行を整える。前後の空白を除き、分岐の行は呼び出しの欄を空にする。先頭の分岐の行
    (元の手順が無い)と、全部の欄が空の行は捨てる。"""
    rows: list[ProcedureStep] = []
    for step in steps:
        values = {name: str(value).strip() for name, value in step if name != "is_branch"}
        if not any(values.values()):
            continue
        if step.is_branch:
            if not rows:
                continue
            values.update(caller="", callee="", call="")
        else:
            values["callee"] = resolve_callee(values["callee"], module_paths)
        rows.append(ProcedureStep(**values, is_branch=step.is_branch))
    return rows


def merge_procedure(
    model: ProcedureModel,
    function_id: str,
    draft: ProcedureDraft,
    module_paths: Sequence[str],
) -> ProcedureModel:
    """1つの処理の手順を、AIの下書きで置き換える(他の処理の手直しはそのまま残す)。

    選定理由は人が書いたものを残し、空のときだけ下書きの値を使う。注記は下書きで置き換える。
    選ばれていない処理の下書きは、末尾に足す(生成の受け付けで、選んだ処理だけに絞っている)。
    """
    steps = _normalize_steps(draft.steps, module_paths)
    procedures: list[Procedure] = []
    found = False
    for procedure in model.procedures:
        if procedure.function_id == function_id and not found:
            found = True
            procedures.append(
                Procedure(
                    function_id=function_id,
                    reason=procedure.reason.strip() or draft.reason.strip(),
                    note=draft.note.strip(),
                    steps=steps,
                )
            )
        else:
            procedures.append(procedure)
    if not found:
        procedures.append(
            Procedure(
                function_id=function_id,
                reason=draft.reason.strip(),
                note=draft.note.strip(),
                steps=steps,
            )
        )
    return ProcedureModel(procedures=procedures)


def pending_function_ids(model: ProcedureModel) -> list[str]:
    """まだ手順の無い(下書きを作っていない)処理の処理ID(選んだ順)。"""
    return [p.function_id for p in model.procedures if not p.steps]


def generation_targets(model: ProcedureModel, requested: Sequence[str] | None) -> list[str]:
    """下書きを作る処理の処理ID。指定が無ければ、まだ手順の無い処理(`pending_function_ids`)。
    指定があれば、前後の空白と重複を除いてその順に使う(タブごとの作り直し)。"""
    if requested is None:
        return pending_function_ids(model)
    return list(dict.fromkeys(r.strip() for r in requested if r.strip()))
