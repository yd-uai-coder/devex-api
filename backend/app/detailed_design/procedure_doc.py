"""段階8 実装手順書の意味モデルと、単位が参照する設計の導出(純粋関数)。

docs/internal_design.md 3.3節「5. 実装手順書」。

段階8は、段階7の作業単位(タスク)ごとの手順書を持つ。`design_stages.model`(段階8)に持つ。

- 作業単位の正本は段階7。単位の ID(`M-01-T01`)は段階7の並び順から導く(`plan_units`)。
  手順書は単位の ID と、作ったときのタスク名を持つ。段階7の並べ替え・改名で ID かタスク名が
  合わなくなった手順書は、検証のエラーにして作り直させる(自動で付け替えない)。
- 手順書は設計を書き写さない。参照する設計(段階5の手順・段階6の関数・段階4のモジュール)は
  単位の処理ID・モジュールから導く(`unit_refs`)。中身の展開は、表示・AI 向けの出力・生成の入力の
  ときにだけ行う。
- 手順書に無い単位は、まだ手順書を生成していない単位。
- AI の指摘(`findings`)は、設計に無いために決められないこと。重要度と、直す先の段階を持つ。
  手順書の上では決めず、対象の段階を直す。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.detailed_design.data_model import DATA_MODEL_STAGE, CrudModel
from app.detailed_design.logic import LOGIC_STAGE, LogicModel, is_drafted, logic_key
from app.detailed_design.plan import PlanModel, PlanTask, milestone_id, task_id
from app.detailed_design.procedure import (
    PROCEDURE_STAGE,
    Procedure,
    ProcedureModel,
    calls_function,
    number_steps,
    step_id,
)
from app.detailed_design.structure import STRUCTURE_STAGE, ModuleListModel

# 実装手順書の段階の番号
PROCEDURE_DOC_STAGE = 8

# 1回の生成で手順書を作る単位の数の上限(1単位 LLM 1回。15分の回収のしきい値に収めるため。
# 段階5・6の上限と同じ値)
MAX_PROCEDURE_DOC_TARGETS = 5

# 実装可能性チェックの指摘の重要度。critical = 最重要、major = 中程度、minor = 軽微
FindingLevel = Literal["critical", "major", "minor"]
FINDING_LEVELS: tuple[FindingLevel, ...] = ("critical", "major", "minor")

# 簡易ドキュメントモードの4文書(指摘の直す先。簡易モードには段階1〜7が無いため、文書で示す)
DesignDocument = Literal[
    "requirements", "external_design", "internal_design", "implementation_plan"
]
DESIGN_DOCUMENT_LABELS: dict[DesignDocument, str] = {
    "requirements": "要件定義書",
    "external_design": "外部設計書",
    "internal_design": "内部設計書",
    "implementation_plan": "実装計画書",
}

# 手順書のファイルの種類。module = 段階4のモジュール(検証する)、test = テスト、
# config = 環境・設定のファイル
UnitFileKind = Literal["module", "test", "config"]


class UnitFile(BaseModel):
    """手順書の、作成・変更するファイル1つ。`basis`は根拠(段階4・段階7の環境・設定のファイルなど)。"""

    path: str
    kind: UnitFileKind = "module"
    responsibility: str = ""
    basis: str = ""


class TestPoint(BaseModel):
    """テスト観点1つ。SUT(テスト対象)・ドライバ・スタブの関係を書く。"""

    viewpoint: str = ""
    sut: str = ""
    driver: str = ""
    stub: str = ""


class AiFinding(BaseModel):
    """手順書を作った AI の指摘1つ(設計に無いため決められないこと)。`fix_stage`は直す先の段階。
    簡易モードでは直す先を文書`fix_document`で示す(`fix_stage`は直す先の段階が無いことを示す8)。"""

    level: FindingLevel = "major"
    target: str = ""
    message: str = ""
    fix_stage: int = Field(default=PROCEDURE_DOC_STAGE, ge=1, le=PROCEDURE_DOC_STAGE)
    fix_document: DesignDocument | None = None


class UnitProcedure(BaseModel):
    """単位1つ分の手順書。`unit_id`と`title`は作ったときの段階7の単位の ID とタスク名。"""

    unit_id: str
    title: str = ""
    purpose: str = ""
    files: list[UnitFile] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    tests: list[TestPoint] = Field(default_factory=list)
    gwt: list[str] = Field(default_factory=list)
    verify: list[str] = Field(default_factory=list)
    findings: list[AiFinding] = Field(default_factory=list)


class ProcedureDocModel(BaseModel):
    """段階8の意味モデル。`units`は手順書のある単位だけ(段階7の並び順)。"""

    units: list[UnitProcedure] = Field(default_factory=list)


@dataclass(frozen=True)
class PlanUnit:
    """段階7の作業単位1つ(ID・マイルストーンの番号・タスク)。"""

    unit_id: str
    milestone: str
    task: PlanTask


def plan_units(plan: PlanModel) -> list[PlanUnit]:
    """段階7の作業単位を、計画の並び順で返す。依存は前の単位だけを指すので、この順が依存順になる。"""
    return [
        PlanUnit(unit_id=task_id(m, t), milestone=milestone_id(m), task=task)
        for m, milestone in enumerate(plan.milestones)
        for t, task in enumerate(milestone.tasks)
    ]


def find_unit(plan: PlanModel, unit_id: str) -> PlanUnit | None:
    """段階7の作業単位を ID で引く(無ければ None)。"""
    return next((unit for unit in plan_units(plan) if unit.unit_id == unit_id.strip()), None)


def documented_unit_ids(plan: PlanModel, model: ProcedureDocModel) -> set[str]:
    """手順書のある単位の ID。単位の ID とタスク名の両方が段階7と合うものだけを数える
    (合わない手順書は検証の UNIT_MISMATCH で、作り直しの対象)。"""
    titles = {(u.unit_id, u.title.strip()) for u in model.units}
    return {u.unit_id for u in plan_units(plan) if (u.unit_id, u.task.title.strip()) in titles}


def generation_targets(
    plan: PlanModel, model: ProcedureDocModel, requested: Sequence[str] | None
) -> list[str]:
    """手順書を作る単位の ID。指定が無ければ、段階7の単位のうち手順書の無いもの(計画の並び順)。
    指定があれば、前後の空白と重複を除いてその順に使う(選んだ単位の生成・作り直し)。"""
    if requested is None:
        documented = documented_unit_ids(plan, model)
        return [u.unit_id for u in plan_units(plan) if u.unit_id not in documented]
    return list(dict.fromkeys(r.strip() for r in requested if r.strip()))


def merge_unit_procedure(
    model: ProcedureDocModel, plan: PlanModel, procedure: UnitProcedure
) -> ProcedureDocModel:
    """単位1つの手順書を置き換える(他の単位の手順書・手直しはそのまま残す)。

    単位は段階7の並び順に並べ直す。段階7に無くなった単位の手順書は消さずに後ろへ置く
    (検証の UNIT_MISMATCH で人に知らせる。自動で付け替えたり消したりしない)。"""
    units = [u for u in model.units if u.unit_id != procedure.unit_id] + [procedure]
    order = {u.unit_id: i for i, u in enumerate(plan_units(plan))}
    planned = sorted((u for u in units if u.unit_id in order), key=lambda u: order[u.unit_id])
    return ProcedureDocModel(units=planned + [u for u in units if u.unit_id not in order])


# 単位が参照する設計の種類。procedure = 段階5の手順(鍵は処理ID)、logic = 段階6の関数
# (鍵は`logic_key`)、module = 段階4のモジュール(鍵はパス。簡易モードでは内部設計書の
# モジュール一覧)、dataflow = 簡易モードの内部設計書の処理別データフロー(鍵は`DF-<n>`)、
# datamodel = 簡易モードの内部設計書 3.2節のデータモデル全体(鍵は`3.2`。DF を持たない単位に添える)
DesignRefKind = Literal["procedure", "logic", "module", "dataflow", "datamodel"]


@dataclass(frozen=True)
class DesignRef:
    """単位が参照する設計の要素1つ。`resolved`は参照先が設計にあるか。
    `via`は参照のもとになった手順ID(段階6の関数だけ。その関数を呼ぶ最初の手順)。"""

    kind: DesignRefKind
    key: str
    resolved: bool
    via: str | None = None


@dataclass(frozen=True)
class DesignIndex:
    """参照の解決に使う、承認済みの段階3〜6の索引。

    - `procedures`: 手順のある処理(処理ID → 手順。段階5)
    - `logic_keys`: 詳細のある関数の`logic_key`(段階6)
    - `logic_functions`: 段階6で選んだ関数の名前(モジュール → 関数名。書き方の揺れの相手を探す)
    - `module_paths`: モジュール一覧のパス(段階4)
    - `crud_functions`: CRUD 図に操作のある処理ID(段階3)
    """

    procedures: Mapping[str, Procedure] = field(default_factory=dict)
    logic_keys: frozenset[str] = frozenset()
    logic_functions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    module_paths: frozenset[str] = frozenset()
    crud_functions: frozenset[str] = frozenset()


def design_index(stages: Mapping[int, Mapping[str, Any]]) -> DesignIndex:
    """入力の段階(段階番号 → 承認済みの`model`)から、参照の解決に使う索引を作る。"""
    procedures = ProcedureModel.model_validate(stages.get(PROCEDURE_STAGE) or {})
    logics = LogicModel.model_validate(stages.get(LOGIC_STAGE) or {})
    modules = ModuleListModel.model_validate(stages.get(STRUCTURE_STAGE) or {})
    crud = CrudModel.model_validate(stages.get(DATA_MODEL_STAGE) or {})
    return DesignIndex(
        procedures={p.function_id.strip(): p for p in procedures.procedures if p.steps},
        logic_keys=frozenset(
            logic_key(row.module, row.function) for row in logics.logics if is_drafted(row)
        ),
        logic_functions=_functions_by_module(logics),
        module_paths=frozenset(row.path.strip() for row in modules.modules),
        crud_functions=frozenset(c.function_id.strip() for c in crud.cells if c.ops.strip()),
    )


def unit_refs(task: PlanTask, index: DesignIndex) -> list[DesignRef]:
    """単位が参照する設計を導く(段階5の手順 → 手順が呼ぶ段階6の関数 → 段階4のモジュールの順)。

    段階6の関数は、単位の処理の手順のうち、モジュールの関数を呼ぶ行(`calls_function`)から導く
    (段階6の候補と同じ規則)。同じ関数は1つにまとめる。"""
    refs: list[DesignRef] = []
    logics: dict[str, DesignRef] = {}
    for function_id in _clean(task.function_ids):
        procedure = index.procedures.get(function_id)
        refs.append(DesignRef("procedure", function_id, procedure is not None))
        if procedure is None:
            continue
        numbers = number_steps(procedure.steps)
        for step, number in zip(procedure.steps, numbers, strict=True):
            if not calls_function(step):
                continue
            key = logic_key(step.callee.strip(), step.call.strip())
            if key not in logics:
                via = step_id(function_id, number)
                logics[key] = DesignRef("logic", key, key in index.logic_keys, via)
    refs.extend(logics.values())
    refs.extend(
        DesignRef("module", path, path in index.module_paths) for path in _clean(task.modules)
    )
    return refs


def name_key(name: str) -> str:
    """関数名の書き方の揺れを除いた比較用の名前(小文字にし、`_`と`-`を除く)。
    `createReservation`と`create_reservation`は同じになる。部分一致には使わない。"""
    return name.strip().lower().replace("_", "").replace("-", "")


def spelling_match(index: DesignIndex, module: str, function: str) -> str | None:
    """手順の呼ぶ関数が段階6に無いとき、同じモジュールの段階6の関数のうち、書き方だけが違うもの
    (`name_key`が等しいもの)の名前を返す。無ければ None(段階6で選ばなかった別の関数)。"""
    wanted = name_key(function)
    for name in index.logic_functions.get(module.strip(), ()):
        if name != function.strip() and name_key(name) == wanted:
            return name
    return None


def _functions_by_module(logics: LogicModel) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    for row in logics.logics:
        found.setdefault(row.module.strip(), []).append(row.function.strip())
    return {module: tuple(names) for module, names in found.items()}


def _clean(values: list[str]) -> list[str]:
    """前後の空白を除き、空と重複を捨てる(順は保つ)。"""
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))
