"""変更要求(CR)を渡して作り直した段階の承認(純粋関数。docs/external_design.md 2.15節)。

CR を渡して段階の下書きを作り直すと、CR の時系列に段階の反映の行(`apply`。`refs`に段階と版)が
残り、段階の行に`generation_change_request_id`が残る。その後の最初の承認で付く印は、CR の変更から
届いたものなので CR に結ぶ(出どころに`cr:<番号>`を足し、CR の印の写しに重ねる)。結んだ印は
検証の対象になり、作り直した段階がまだ承認されていない間も検証済みにできない
(verification.py の`pending_stages`)。

「承認していない」は、段階の行が今もその CR で作り直したもので(後で CR なしで作り直したら外れる)、
承認した版が無いか、反映の行の版より前のとき。承認は版を増やさないので、作り直した版を承認すると
承認した版がその版になり、外れる。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.change_requests.states import ChangeRequestAction

# 段階の承認で印を結んだ記録の行(状態を変えない。遷移の表には持たない)
APPROVE_STAGE_ACTION: ChangeRequestAction = "approve_stage"


@dataclass(frozen=True)
class StageFacts:
    """段階の行の値(DB の行から写したもの)。"""

    stage: int
    generation_change_request_id: Any
    approved_version: int | None


def stage_apply_versions(
    events: Iterable[tuple[str, Mapping[str, Any] | None]],
) -> dict[int, int]:
    """CR の時系列の行(操作, refs)から、段階の反映の行の段階ごとの最新の版。文書の反映の行
    (`refs`に段階が無い)と形の正しくない行は読まない。"""
    versions: dict[int, int] = {}
    for action, refs in events:
        if action != "apply" or not refs:
            continue
        stage, version = refs.get("stage"), refs.get("version")
        if isinstance(stage, int) and isinstance(version, int):
            versions[stage] = max(version, versions.get(stage, version))
    return versions


def pending_stages(
    change_request_id: Any,
    applied_versions: Mapping[int, int],
    stages: Iterable[StageFacts],
) -> list[int]:
    """CR で作り直した後に、まだ承認していない段階(段階の順)。"""
    found = []
    for row in stages:
        version = applied_versions.get(row.stage)
        if version is None or row.generation_change_request_id != change_request_id:
            continue
        if row.approved_version is None or row.approved_version < version:
            found.append(row.stage)
    return sorted(found)
