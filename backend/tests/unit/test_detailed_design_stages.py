from app.detailed_design import (
    STAGE_INPUTS,
    STAGES,
    StageRecord,
    can_approve,
    current_inputs,
    derive_states,
)
from app.detailed_design.stages import SIMPLE_DOCUMENTS, SIMPLE_STAGE_INPUTS, stage_inputs

DOCS = {"requirements": 1, "external_design": 1}


def _approved(version: int, fingerprint: dict[str, int | None]) -> StageRecord:
    return StageRecord(
        status="approved", version=version, approved_version=version, input_fingerprint=fingerprint
    )


def test_stage_inputs_only_refer_to_earlier_stages() -> None:
    assert tuple(STAGE_INPUTS) == STAGES
    for stage, inputs in STAGE_INPUTS.items():
        assert all(s < stage for s in inputs.stages)


def test_without_documents_every_stage_is_not_started_and_stage1_is_locked() -> None:
    views = derive_states({}, {})

    assert {v.state for v in views.values()} == {"not_started"}
    assert views[1].missing_inputs == ("doc:external_design",)
    assert views[1].is_open is False


def test_stage1_opens_when_external_design_exists_and_stage2_waits_for_stage1() -> None:
    views = derive_states({}, DOCS)

    assert views[1].is_open is True
    assert views[2].missing_inputs == ("stage:1",)


def test_approved_stage_with_matching_fingerprint_opens_next_stage() -> None:
    records = {1: _approved(2, {"doc:external_design": 1})}

    views = derive_states(records, DOCS)

    assert views[1].state == "approved"
    assert views[2].is_open is True


def test_changed_document_marks_stage_outdated_and_locks_next_stage() -> None:
    records = {1: _approved(2, {"doc:external_design": 1})}

    views = derive_states(records, {"requirements": 1, "external_design": 2})

    assert views[1].state == "outdated"
    assert views[2].missing_inputs == ("stage:1",)


def test_restored_lower_document_version_is_also_outdated() -> None:
    """「等しくない」で比べる(復元で版の番号が下がっても古いと分かる)。"""
    records = {1: _approved(2, {"doc:external_design": 3})}

    views = derive_states(records, {"requirements": 1, "external_design": 2})

    assert views[1].state == "outdated"


def test_editing_an_earlier_stage_propagates_outdated_to_later_stages() -> None:
    records = {
        1: StageRecord(
            status="reviewing",
            version=3,
            approved_version=2,
            input_fingerprint={"doc:external_design": 1},
        ),
        2: _approved(1, {"stage:1": 2, "doc:requirements": 1}),
    }

    views = derive_states(records, DOCS)

    assert views[1].state == "reviewing"
    assert views[2].state == "outdated"


def test_current_inputs_lists_stages_then_documents() -> None:
    assert current_inputs(
        4, approved_stage_versions={1: 1, 2: 4}, doc_versions={"requirements": 2}
    ) == {"stage:1": 1, "stage:2": 4, "stage:3": None, "doc:requirements": 2}


def test_can_approve_allows_reapproving_only_outdated_stages() -> None:
    assert can_approve("draft") is True
    assert can_approve("regenerated") is True
    assert can_approve("reviewing") is True
    assert can_approve("outdated") is True
    assert can_approve("approved") is False
    assert can_approve("not_started") is False


def test_regenerated_stage_keeps_its_state_until_inputs_change() -> None:
    """作り直した段階は、生成時の入力の記録と一致すれば「再生成済」のまま、
    食い違えば「古い」になる。"""
    record = StageRecord(
        status="regenerated",
        version=3,
        approved_version=2,
        input_fingerprint={"doc:external_design": 2},
    )

    current = derive_states({1: record}, {**DOCS, "external_design": 2})
    changed = derive_states({1: record}, {**DOCS, "external_design": 3})

    assert current[1].state == "regenerated"
    assert changed[1].state == "outdated"


def test_simple_mode_has_only_stage8_reading_four_documents() -> None:
    """簡易モードは段階8だけ。4文書がそろうと開き、詳細設計モードの段階の行は読まない。"""
    assert stage_inputs("simple") == SIMPLE_STAGE_INPUTS
    assert stage_inputs("detailed") == STAGE_INPUTS
    docs = dict.fromkeys(SIMPLE_DOCUMENTS, 1)
    stray = {1: _approved(1, {"doc:external_design": 1})}

    views = derive_states(stray, docs, SIMPLE_STAGE_INPUTS)

    assert list(views) == [8]
    assert views[8].is_open is True
    assert views[8].state == "not_started"
    locked = derive_states({}, {**docs, "implementation_plan": None}, SIMPLE_STAGE_INPUTS)
    assert locked[8].missing_inputs == ("doc:implementation_plan",)


def test_simple_mode_stage8_is_outdated_when_a_document_is_regenerated() -> None:
    docs = dict.fromkeys(SIMPLE_DOCUMENTS, 1)
    fingerprint = current_inputs(
        8, approved_stage_versions={}, doc_versions=docs, inputs=SIMPLE_STAGE_INPUTS
    )
    records = {8: _approved(1, fingerprint)}

    assert derive_states(records, docs, SIMPLE_STAGE_INPUTS)[8].state == "approved"
    regenerated = {**docs, "requirements": 2}
    assert derive_states(records, regenerated, SIMPLE_STAGE_INPUTS)[8].state == "outdated"
