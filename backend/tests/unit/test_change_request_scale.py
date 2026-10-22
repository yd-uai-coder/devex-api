"""変更要求(CR)の変更の規模(作業単位・ファイル・テスト・画面・API の数)のテスト。

SUT: app.change_requests の predicted_scale / count_scale / sources_from_snapshots /
     unlinked_requirements / compare_scale / display_name と、Scale・ScaleCount・ScaleRemark・
     ScaleSources・ScaleComparisonRow
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため。入力は、要件 FR-001・FR-002・
NFR-001 と段階1〜8 を持つ fixture の model を束の写しの形に並べ、テスト仕様書の写しを足したもの
"""

from tests.fixtures.change_impact import REPOSITORY, ROUTE, SERVICE, impact_documents, impact_stages

from app.change_requests import (
    SCALE_KINDS,
    ScaleSources,
    Target,
    compare_scale,
    count_scale,
    display_name,
    predict_from_snapshots,
    predicted_scale,
    sources_from_snapshots,
    unlinked_requirements,
)


def _snapshots(*, test_spec: bool = True) -> dict[str, dict]:
    stages = {f"stage:{n}": model for n, model in impact_stages().items()}
    snapshots: dict[str, dict] = {**impact_documents(), **stages}
    if test_spec:
        snapshots["test_spec"] = {
            "integration": [
                {"id": "IT-001", "functions": ["F-01"], "req_ids": []},
                {"id": "IT-002", "functions": ["F-02"], "req_ids": []},
            ],
            "system": [{"id": "ST-001", "functions": [], "req_ids": ["FR-001"]}],
        }
    return snapshots


def _scale(
    targets: list[Target],
    snapshots: dict[str, dict] | None = None,
    states: dict[int, str] | None = None,
    *,
    adds_requirement: bool = False,
):
    snapshots = snapshots if snapshots is not None else _snapshots()
    prediction = predict_from_snapshots(snapshots, targets, adds_requirement=adds_requirement)
    return predicted_scale(targets, prediction, snapshots, states or {})


def test_counts_every_kind_from_the_prediction() -> None:
    """予測の作業単位(段階7の名前と ID)・そのファイル(パスで重なりを除く)・処理か対象の要件に
    当たるテスト・画面・API を数える。種類は決まった順。"""
    scale = _scale([Target("FR-001")])

    assert tuple(c.kind for c in scale.counts) == SCALE_KINDS
    assert scale.of("unit").names == ("予約を登録する(M-01-T02)",)
    assert scale.of("file").names == (ROUTE, SERVICE, REPOSITORY)
    assert scale.of("test").names == ("IT-001", "ST-001")
    assert (scale.integration_tests, scale.system_tests) == (("IT-001",), ("ST-001",))
    assert scale.of("screen").names == ("予約画面(SCR-001)",)
    assert scale.of("api").names == ("POST /api/v1/reservations",)
    assert [c.count for c in scale.counts] == [1, 3, 2, 1, 1]
    assert scale.uncountable_kinds == ()


def test_files_shared_by_units_are_counted_once() -> None:
    """2つの作業単位が同じ3つのファイルを触っても、ファイルは3件。"""
    scale = _scale([Target("FR-001"), Target("FR-002")])

    assert scale.of("unit").count == 2
    assert scale.of("file").count == 3


def test_units_without_procedure_doc_are_counted_from_the_plan() -> None:
    """段階8 に手順書の無い作業単位は、段階7 のモジュールから数え、注記に単位の ID を出す。"""
    snapshots = _snapshots()
    snapshots["stage:8"] = {"units": []}
    sources = sources_from_snapshots(snapshots, {})

    scale = count_scale([("task:U-002", "M-01-T02 予約を登録する")], sources)

    assert scale.of("file").names == (ROUTE, SERVICE, REPOSITORY)
    notes = scale.of("file").notes
    assert [(n.code, n.detail) for n in notes] == [("planned_modules_only", "M-01-T02")]


def test_requirement_without_function_is_uncountable() -> None:
    """機能要件が処理に1つも届かなければ(処理にも API にも要件 ID が無い)、作業単位・ファイル・
    テストは数えられない(0件と区別する)。数えられない種類には、古い段階の注記を出さない。画面は
    文書の側なので数える。"""
    snapshots = _snapshots()
    for function in snapshots["stage:1"]["functions"]:
        function["req_ids"] = []
    for api in snapshots["external_design"]["apis"]:
        api["req_ids"] = []

    scale = _scale([Target("FR-001")], snapshots, states={1: "outdated", 7: "outdated"})

    for kind in ("unit", "file", "test"):
        count = scale.of(kind)
        assert count.count is None
        assert count.notes == ()
        assert [(r.code, r.detail) for r in count.uncountable] == [
            ("requirement_unlinked", "FR-001")
        ]
    assert scale.of("screen").count == 1


def test_non_functional_requirement_is_not_unlinked() -> None:
    """非機能要件は処理に届かなくても、つながらないとはみなさない(横断事項などに届くため)。"""
    targets = [Target("NFR-001")]
    snapshots = _snapshots()
    prediction = predict_from_snapshots(snapshots, targets, adds_requirement=False)

    assert unlinked_requirements(targets, prediction) == ()
    assert _scale(targets).of("unit").count is not None


def test_missing_stage_and_documents_are_uncountable() -> None:
    """段階1・7 が束に無ければ作業単位・ファイル、段階1 かテスト仕様書が無ければテスト、外部設計書が
    無ければ画面・API を数えられない。段階1 が無いときは、要件のつながりの理由は出さない。"""
    snapshots = _snapshots(test_spec=False)
    del snapshots["stage:1"]
    del snapshots["external_design"]

    scale = _scale([Target("FR-001")], snapshots)

    assert [(r.code, r.detail) for r in scale.of("unit").uncountable] == [
        ("stage_missing", "段階1")
    ]
    assert [(r.code, r.detail) for r in scale.of("test").uncountable] == [
        ("stage_missing", "段階1"),
        ("document_missing", "テスト仕様書"),
    ]
    assert [(r.code, r.detail) for r in scale.of("api").uncountable] == [
        ("document_missing", "外部設計書")
    ]
    assert not scale.has_test_spec


def test_outdated_stage_is_counted_with_a_note() -> None:
    """束の段階が承認のままでなくても数え、その段階を使う種類に注記する。"""
    scale = _scale([Target("FR-001")], states={1: "approved", 7: "outdated", 8: "draft"})

    assert scale.of("unit").count == 1
    assert [(n.code, n.detail) for n in scale.of("unit").notes] == [
        ("stage_outdated", "段階7")
    ]
    assert [(n.code, n.detail) for n in scale.of("file").notes] == [
        ("stage_outdated", "段階7・段階8")
    ]
    assert scale.of("test").notes == ()


def test_new_requirement_without_origin_makes_every_kind_uncountable() -> None:
    scale = _scale([], adds_requirement=True)

    assert scale.uncountable_kinds == SCALE_KINDS
    assert scale.of("screen").uncountable[0].code == "new_requirement_unpredicted"


def test_compare_scale_puts_actual_next_to_predicted() -> None:
    """作業単位・ファイル・テストは印から数えた実際の数、画面・API は確認の一覧で確かめた数。"""
    predicted = _scale([Target("FR-001")])
    sources = ScaleSources(
        plan=sources_from_snapshots(_snapshots(), {}).plan,
        procedure_docs=None,
        test_spec={"integration": [], "system": []},
        has_external_design=True,
    )
    actual = count_scale([("task:U-002", "M-01-T02 予約を登録する")], sources)

    rows = compare_scale(
        predicted,
        actual,
        ["screen:SCR-001", "api:POST /api/v1/reservations"],
        ["screen:SCR-001"],
    )

    by_kind = {row.kind: (row.predicted, row.actual, row.basis) for row in rows}
    assert by_kind == {
        "unit": (1, 1, "marks"),
        "file": (3, 3, "marks"),
        "test": (2, 0, "marks"),
        "screen": (1, 1, "document_checks"),
        "api": (1, 0, "document_checks"),
    }
    assert rows[0].label == "作業単位"


def test_display_name_puts_the_name_first() -> None:
    assert display_name("SCR-001 トップページ") == "トップページ(SCR-001)"
    assert display_name("M-01-T02 予約の登録") == "予約の登録(M-01-T02)"
    assert display_name("M-01-T02 の手順書") == "M-01-T02 の手順書"
    assert display_name("F-01 × reservations") == "F-01 × reservations"
    assert display_name("GET /api/v1/reservations") == "GET /api/v1/reservations"
    assert display_name("app/services/reservation.py") == "app/services/reservation.py"
