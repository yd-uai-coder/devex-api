"""詳細設計モードの段階(1〜7)の状態と陳腐化を決める純粋関数(docs/external_design.md 2.7節)。

DBに保存する状態は`draft`/`regenerated`/`reviewing`/`approved`の4つだけで、画面に出す6つの
状態のうち「未着手」(行が無い)と「古い」(入力が承認時・生成時から変わった)は、ここで導く。
`regenerated`(再生成済・未承認)は、内容のある段階をAIが作り直したときの状態(Phase 16)。

陳腐化は Phase 13 の`app/uml/sync/staleness.py`と同じく「等しくない」で比べる。段階を承認したとき、
その段階が入力にしたもの(前の段階の承認済みの版・文書の表示中の版)を`input_fingerprint`に
記録しておき、今の値と1つでも違えば古いとする(文書の復元では版の番号が下がることがあるため、
「大きい/小さい」では比べない)。

前の段階が承認済みでない(未着手・下書き・レビュー中・古い)とき、その段階の「今の値」は`None`に
する。そのため、前の段階を編集した時点で後ろの段階に「古い」が出て、古さは後ろへ順に伝わる。
後ろの段階を自動で作り直すことはしない(再生成するか、このまま承認し直すかは人が選ぶ)。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

STAGES: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)

StoredStatus = Literal["draft", "regenerated", "reviewing", "approved"]
StageState = Literal["not_started", "draft", "regenerated", "reviewing", "approved", "outdated"]
Fingerprint = dict[str, int | None]


@dataclass(frozen=True)
class StageInputs:
    """1つの段階が入力にする、前の段階と文書。"""

    stages: tuple[int, ...]
    documents: tuple[str, ...]


# docs/external_design.md 2.7節の段階表の「入力」列。段階4の「技術スタック」は要件定義
# (1.6 制約条件・前提条件)から読むため、要件定義を入力に含める。
STAGE_INPUTS: dict[int, StageInputs] = {
    1: StageInputs(stages=(), documents=("external_design",)),
    2: StageInputs(stages=(1,), documents=("requirements",)),
    3: StageInputs(stages=(2,), documents=()),
    4: StageInputs(stages=(1, 2, 3), documents=("requirements",)),
    5: StageInputs(stages=(2, 4), documents=()),
    6: StageInputs(stages=(5,), documents=()),
    7: StageInputs(stages=(1, 2, 3, 4, 5, 6), documents=("requirements", "external_design")),
}


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
) -> Fingerprint:
    """段階`stage`が今入力にしているものの版を返す。承認済みでない段階・まだ無い文書は`None`。"""
    inputs = STAGE_INPUTS[stage]
    fingerprint: Fingerprint = {
        stage_key(s): approved_stage_versions.get(s) for s in inputs.stages
    }
    fingerprint.update({doc_key(d): doc_versions.get(d) for d in inputs.documents})
    return fingerprint


def derive_states(
    records: Mapping[int, StageRecord], doc_versions: Mapping[str, int | None]
) -> dict[int, StageView]:
    """全段階の状態を、段階の順に決める(前の段階の結果が後ろの段階の入力になるため)。"""
    approved_versions: dict[int, int | None] = {}
    views: dict[int, StageView] = {}
    for stage in STAGES:
        current = current_inputs(
            stage, approved_stage_versions=approved_versions, doc_versions=doc_versions
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
