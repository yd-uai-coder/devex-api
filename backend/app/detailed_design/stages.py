"""詳細設計モードの段階(1〜8)の状態と陳腐化を決める純粋関数(docs/external_design.md 2.7節)。

DBに保存する状態は`draft`/`regenerated`/`reviewing`/`approved`の4つだけで、画面に出す6つの
状態のうち「未着手」(行が無い)と「古い」(入力が承認時・生成時から変わった)は、ここで導く。
`regenerated`(再生成済・未承認)は、内容のある段階をAIが作り直したときの状態。

陳腐化は「等しくない」で比べる。段階を承認したとき、
その段階が入力にしたもの(前の段階の承認済みの版・文書の表示中の版)を`input_fingerprint`に
記録しておき、今の値と1つでも違えば古いとする(文書の復元では版の番号が下がることがあるため、
「大きい/小さい」では比べない)。

前の段階が承認済みでない(未着手・下書き・レビュー中・古い)とき、その段階の「今の値」は`None`に
する。そのため、前の段階を編集した時点で後ろの段階に「古い」が出て、古さは後ろへ順に伝わる。
後ろの段階を自動で作り直すことはしない(再生成するか、このまま承認し直すかは人が選ぶ)。

簡易ドキュメントモードのプロジェクトは、段階8(実装手順書)だけを持つ。入力は段階でなく、生成済みの
4文書(`SIMPLE_STAGE_INPUTS`)。どれかの文書を再生成・復元すると、段階8は「古い」になる。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

STAGES: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8)

# プロジェクトのモード(作成後は変えない)。simple = 簡易ドキュメントモード、detailed = 詳細設計モード
ProjectMode = Literal["simple", "detailed"]

StoredStatus = Literal["draft", "regenerated", "reviewing", "approved"]
StageState = Literal["not_started", "draft", "regenerated", "reviewing", "approved", "outdated"]
Fingerprint = dict[str, int | None]


@dataclass(frozen=True)
class StageInputs:
    """1つの段階が入力にする、前の段階と文書。"""

    stages: tuple[int, ...]
    documents: tuple[str, ...]


# docs/external_design.md 2.7節の段階表の「入力」列。段階4の「技術スタック」は要件定義
# (1.6 制約条件・前提条件)から読むため、要件定義を入力に含める。段階8(実装手順書)の要件定義は、
# 手順書の対象外(Should / Could / Won't)を書くために読む。
STAGE_INPUTS: dict[int, StageInputs] = {
    1: StageInputs(stages=(), documents=("external_design",)),
    2: StageInputs(stages=(1,), documents=("requirements",)),
    3: StageInputs(stages=(2,), documents=()),
    4: StageInputs(stages=(1, 2, 3), documents=("requirements",)),
    5: StageInputs(stages=(2, 4), documents=()),
    6: StageInputs(stages=(5,), documents=()),
    7: StageInputs(stages=(1, 2, 3, 4, 5, 6), documents=("requirements", "external_design")),
    8: StageInputs(stages=(1, 2, 3, 4, 5, 6, 7), documents=("requirements",)),
}

# 簡易ドキュメントモードの段階(段階8だけ)と入力。段階8は4文書をすべて読む(作業単位は実装計画書の
# WBS、参照する設計は内部設計書・外部設計書、対象外は要件定義書から)。
SIMPLE_DOCUMENTS: tuple[str, ...] = (
    "requirements",
    "external_design",
    "internal_design",
    "implementation_plan",
)
SIMPLE_STAGE_INPUTS: dict[int, StageInputs] = {
    8: StageInputs(stages=(), documents=SIMPLE_DOCUMENTS)
}


def stage_inputs(mode: str) -> Mapping[int, StageInputs]:
    """モードの段階と入力(段階の順)。詳細設計モードは段階1〜8、簡易モードは段階8だけ。"""
    return STAGE_INPUTS if mode == "detailed" else SIMPLE_STAGE_INPUTS


@dataclass(frozen=True)
class StageRecord:
    """DBに保存された段階1行分のうち、状態の判定に使う値。"""

    status: StoredStatus
    version: int
    approved_version: int | None
    input_fingerprint: Mapping[str, int | None] | None


@dataclass(frozen=True)
class StageView:
    """段階1つ分の、画面に出す状態。`missing_inputs`が空なら、その段階は開いている(編集・承認できる)。"""

    stage: int
    state: StageState
    missing_inputs: tuple[str, ...]

    @property
    def is_open(self) -> bool:
        return not self.missing_inputs


def stage_key(stage: int) -> str:
    return f"stage:{stage}"


def doc_key(doc_type: str) -> str:
    return f"doc:{doc_type}"


def current_inputs(
    stage: int,
    *,
    approved_stage_versions: Mapping[int, int | None],
    doc_versions: Mapping[str, int | None],
    inputs: Mapping[int, StageInputs] = STAGE_INPUTS,
) -> Fingerprint:
    """段階`stage`が今入力にしているものの版を返す。承認済みでない段階・まだ無い文書は`None`。
    `inputs`はモードの段階と入力(`stage_inputs`。既定は詳細設計モード)。"""
    stage_input = inputs[stage]
    fingerprint: Fingerprint = {
        stage_key(s): approved_stage_versions.get(s) for s in stage_input.stages
    }
    fingerprint.update({doc_key(d): doc_versions.get(d) for d in stage_input.documents})
    return fingerprint


def derive_states(
    records: Mapping[int, StageRecord],
    doc_versions: Mapping[str, int | None],
    inputs: Mapping[int, StageInputs] = STAGE_INPUTS,
) -> dict[int, StageView]:
    """モードの全段階の状態を、段階の順に決める(前の段階の結果が後ろの段階の入力になるため)。
    `inputs`に無い段階の行(`records`)は読まない。"""
    approved_versions: dict[int, int | None] = {}
    views: dict[int, StageView] = {}
    for stage in inputs:
        current = current_inputs(
            stage,
            approved_stage_versions=approved_versions,
            doc_versions=doc_versions,
            inputs=inputs,
        )
        record = records.get(stage)
        state = _state_of(record, current)
        views[stage] = StageView(
            stage=stage,
            state=state,
            missing_inputs=tuple(key for key, version in current.items() if version is None),
        )
        approved_versions[stage] = (
            record.approved_version if record is not None and state == "approved" else None
        )
    return views


def _state_of(record: StageRecord | None, current: Fingerprint) -> StageState:
    if record is None:
        return "not_started"
    if record.input_fingerprint is not None and dict(record.input_fingerprint) != current:
        return "outdated"
    return record.status


def can_approve(state: StageState) -> bool:
    """承認できる状態か。承認済みでも「古い」なら、内容を変えずに承認し直せる(入力の版を記録し直す)。"""
    return state in ("draft", "regenerated", "reviewing", "outdated")
