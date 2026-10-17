"""テスト仕様書の決定的な検証のテスト。

SUT は`app/documents/test_spec/validation.py`の`validate_test_spec`・`is_outdated`・`row_counts`・
`design_gap_count`、
ドライバはこのテスト。スタブ不要 ── 対象が純粋関数で、model だけから決まるため。
"""

from tests.fixtures.spec_documents import (
    DELETE_API,
    LIST_API,
    spec_draft,
    spec_external,
    spec_requirements,
)

from app.documents.test_spec import (
    SpecCase,
    TestSpecModel,
    build_model,
    design_gap_count,
    is_outdated,
    row_counts,
    validate_test_spec,
)


def _model() -> TestSpecModel:
    return build_model(
        spec_draft(),
        requirements=spec_requirements(),
        external=spec_external(),
        links={},
        inputs={"doc:requirements": 1},
    )


def _codes(model: TestSpecModel) -> dict[str, tuple[str, ...]]:
    return {i.code: i.targets for i in validate_test_spec(model)}


def test_sample_finds_untested_must_and_apis_without_error_tests():
    assert _codes(_model()) == {
        "MUST_REQUIREMENT_UNTESTED": ("FR-002",),
        "API_WITHOUT_ERROR_TEST": (LIST_API, DELETE_API),
    }


def test_levels():
    levels = {i.code: i.level for i in validate_test_spec(_model())}
    assert levels == {"MUST_REQUIREMENT_UNTESTED": "major", "API_WITHOUT_ERROR_TEST": "minor"}


def test_unknown_references():
    model = _model()
    model.system.append(
        SpecCase(id="ST-002", level="system", target="x", viewpoint="正常",
                 req_ids=["FR-009"], screens=["SCR-009"])
    )
    model.integration.append(
        SpecCase(id="IT-004", level="integration", target="PUT /api/v1/other", viewpoint="異常")
    )
    codes = _codes(model)
    assert codes["UNKNOWN_REQUIREMENT"] == ("FR-009",)
    assert codes["UNKNOWN_SCREEN"] == ("SCR-009",)
    assert codes["UNKNOWN_API"] == ("PUT /api/v1/other",)


def test_without_snapshots_skips_reference_checks():
    model = TestSpecModel(
        integration=[SpecCase(id="IT-001", level="integration", target="GET /x", viewpoint="正常",
                              req_ids=["FR-009"], screens=["SCR-009"])]
    )
    assert validate_test_spec(model) == []


def test_is_outdated_and_counts():
    model = _model()
    assert not is_outdated(model, {"doc:requirements": 1})
    assert is_outdated(model, {"doc:requirements": 2})
    assert not is_outdated(TestSpecModel(), {"doc:requirements": 2})
    assert row_counts(model) == {"integration": 3, "system": 1}
    assert design_gap_count(model) == 1


def test_shallow_requirement_tests():
    """要件 ID を持つ総合テストで、期待結果が要件の言い直し(「要件どおり」「提供されること」)なら
    軽微の指摘。何が見える・残るかを書いたもの・結合テストは指摘しない。"""
    model = _model()
    model.system += [
        SpecCase(id="ST-002", level="system", target="FR-001", viewpoint="正常",
                 expected="FR-001 の要件どおり予約できること", req_ids=["FR-001"]),
        SpecCase(id="ST-003", level="system", target="FR-001", viewpoint="正常",
                 expected="予約の機能が提供されること", req_ids=["FR-001"]),
        SpecCase(id="ST-004", level="system", target="FR-001", viewpoint="正常",
                 expected="一覧に 5/1 の予約が出て、DB に1件残る", req_ids=["FR-001"]),
        SpecCase(id="ST-005", level="system", target="性能", viewpoint="非機能",
                 expected="正しく動作すること"),
    ]
    issues = {i.code: i for i in validate_test_spec(model)}
    assert issues["SHALLOW_REQUIREMENT_TEST"].targets == ("ST-002", "ST-003")
    assert issues["SHALLOW_REQUIREMENT_TEST"].level == "minor"
