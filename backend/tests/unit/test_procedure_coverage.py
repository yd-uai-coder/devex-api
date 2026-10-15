"""段階8 実装可能性チェックの、単位が作るファイルの網羅と順序の判定のテスト。

SUT は`app/detailed_design/procedure_coverage.py`の各関数、ドライバはこのテスト。
スタブ不要 ── 対象が純粋関数で、引数(単位・手順書・モジュール一覧の行)だけから決まるため。
"""

import pytest

from app.detailed_design.plan import PlanTask
from app.detailed_design.procedure_coverage import (
    LateDependency,
    TableCoverage,
    UnitFileRef,
    error_test_gap,
    has_test_file,
    is_schema_path,
    is_test_path,
    late_dependencies,
    table_coverage,
    table_mentioned,
    unit_files,
)
from app.detailed_design.procedure_doc import PlanUnit, UnitFile, UnitProcedure

# Test で始まる名前は pytest がテストのクラスとして集めようとするので、別名にする
from app.detailed_design.procedure_doc import TestPoint as Point
from app.detailed_design.structure import ModuleRow

ROUTE = "app/api/routes/auth.py"
SERVICE = "app/services/ai_service.py"
MAIN = "app/main.py"
ROWS = [
    ModuleRow(path=MAIN, responsibility="アプリの組み立て", depends_on=[ROUTE]),
    ModuleRow(path=ROUTE, responsibility="認証の API", depends_on=["fastapi", SERVICE]),
    ModuleRow(path=SERVICE, responsibility="AI の呼び出し", depends_on=["langchain"]),
]
BY_PATH = {row.path: row for row in ROWS}


def _row_of(path: str) -> ModuleRow | None:
    return BY_PATH.get(path)


def _unit(uid: str, *, modules: list[str], config: list[str] | None = None) -> PlanUnit:
    task = PlanTask(title=uid, modules=modules, config_files=config or [])
    return PlanUnit(unit_id=uid, milestone=uid[:4], task=task)


def test_unit_files_merges_procedure_and_plan() -> None:
    """統合スモーク: 手順書のファイルを先に(責務つき)、段階7のモジュール・設定を後に、重複は1つ。"""
    unit = _unit("M-01-T01", modules=[ROUTE, SERVICE], config=["Dockerfile"])
    doc = UnitProcedure(
        unit_id="M-01-T01",
        files=[
            UnitFile(path=ROUTE, responsibility="ログイン"),
            UnitFile(path="tests/test_auth.py", kind="test"),
        ],
    )

    files = unit_files(unit, doc, _row_of)

    assert files == [
        UnitFileRef(ROUTE, "module", "ログイン"),
        UnitFileRef("tests/test_auth.py", "test", ""),
        UnitFileRef(SERVICE, "module", "AI の呼び出し"),
        UnitFileRef("Dockerfile", "config", ""),
    ]
    assert unit_files(unit, None, _row_of)[0] == UnitFileRef(ROUTE, "module", "認証の API")


def test_dependency_built_by_a_later_unit_is_reported_once() -> None:
    units = [("M-01-T01", [ROUTE]), ("M-02-T01", [ROUTE]), ("M-03-T01", [SERVICE])]

    found = late_dependencies(units, ROWS, _row_of)

    assert found == [
        LateDependency("M-01-T01", ROUTE, SERVICE, "M-03-T01"),
        LateDependency("M-02-T01", ROUTE, SERVICE, "M-03-T01"),
    ]


def test_dependency_built_earlier_or_together_is_not_reported() -> None:
    together = [("M-01-T01", [SERVICE, ROUTE])]
    earlier = [("M-01-T01", [SERVICE]), ("M-01-T02", [ROUTE])]

    assert late_dependencies(together, ROWS, _row_of) == []
    assert late_dependencies(earlier, ROWS, _row_of) == []


def test_external_unbuilt_and_exempt_dependencies_are_not_reported() -> None:
    """外部のライブラリ・どの単位も作らない行・基盤の単位(`exempt`)の依存は見ない。"""
    unbuilt = [("M-01-T01", [ROUTE])]
    base_main = [("M-01-T01", [MAIN]), ("M-01-T02", [ROUTE, SERVICE])]

    assert late_dependencies(unbuilt, ROWS, _row_of) == []
    assert late_dependencies(base_main, ROWS, _row_of, {"M-01-T01"}) == []
    # 基盤でなければ、組み立てのファイルも後の単位のルートへの依存として出る
    assert [d.unit for d in late_dependencies(base_main, ROWS, _row_of)] == ["M-01-T01"]


def test_dependency_wired_when_built_is_not_reported() -> None:
    """依存先を作る単位か後の単位が、依存する側のファイルも変更するなら、そこでつなぐとみなす。"""
    wired = [("M-01-T01", [ROUTE]), ("M-01-T02", [SERVICE, ROUTE])]
    wired_later = [("M-01-T01", [ROUTE]), ("M-01-T02", [SERVICE]), ("M-01-T03", [ROUTE])]

    assert late_dependencies(wired, ROWS, _row_of) == []
    assert late_dependencies(wired_later, ROWS, _row_of) == []


def test_library_does_not_match_a_wildcard_row() -> None:
    """外部のライブラリ(`FastAPI`)は、層の行(`app/api/*.py`)の`*`だけには当たらない。"""
    rows = [
        ModuleRow(path="app/api/*.py", depends_on=["FastAPI", "app/services"]),
        ModuleRow(path="app/*"),
        ModuleRow(path="app/services/*.py"),
    ]
    row_of = {"app/api/x.py": rows[0], "app/main.py": rows[1], "app/services/y.py": rows[2]}.get
    units = [("M-01-T01", ["app/api/x.py"]), ("M-01-T02", ["app/main.py", "app/services/y.py"])]

    assert late_dependencies(units, rows, row_of) == [
        LateDependency("M-01-T01", "app/api/x.py", "app/services", "M-01-T02")
    ]


def test_directory_dependency_uses_the_earliest_builder() -> None:
    """ディレクトリの依存先は複数の行に当たる。どれか1つが先に作られていれば指摘しない。"""
    other = "app/services/user_service.py"
    rows = [
        ModuleRow(path=ROUTE, depends_on=["app/services"]),
        ModuleRow(path=SERVICE),
        ModuleRow(path=other),
    ]
    row_of = {row.path: row for row in rows}.get

    early = [("M-01-T01", [other]), ("M-01-T02", [ROUTE]), ("M-01-T03", [SERVICE])]
    late = [("M-01-T01", [ROUTE]), ("M-01-T02", [SERVICE, other])]

    assert late_dependencies(early, rows, row_of) == []
    assert late_dependencies(late, rows, row_of) == [
        LateDependency("M-01-T01", ROUTE, "app/services", "M-01-T02")
    ]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_auth.py", True),
        ("backend/tests/unit/helpers.py", True),
        ("app/auth_test.py", True),
        ("src/features/auth/__tests__/Login.test.tsx", True),
        ("e2e/login.spec.ts", True),
        ("tests/conftest.py", True),
        ("app/services/testing_service.py", False),
        ("app/api/routes/auth.py", False),
        ("", False),
    ],
)
def test_is_test_path(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


def test_has_test_file_by_kind_or_path() -> None:
    by_kind = UnitProcedure(unit_id="U", files=[UnitFile(path="spec/auth.py", kind="test")])
    by_path = UnitProcedure(unit_id="U", files=[UnitFile(path="tests/test_auth.py")])
    none = UnitProcedure(unit_id="U", files=[UnitFile(path=ROUTE)])

    assert (has_test_file(by_kind), has_test_file(by_path), has_test_file(none)) == (
        True,
        True,
        False,
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("app/models/user.py", True),
        ("backend/app/models.py", True),
        ("alembic/versions/0001_init.py", True),
        ("db/migrations/0001.sql", True),
        ("prisma/schema.prisma", True),
        ("src/entities/User.ts", True),
        ("app/schemas/user.py", False),
        ("backend/core/security.py", False),
    ],
)
def test_is_schema_path(path: str, expected: bool) -> None:
    assert is_schema_path(path) is expected


def test_table_mentioned_accepts_singular_and_separators() -> None:
    assert table_mentioned("users", ["app/models/user.py"])
    assert table_mentioned("chat_histories", ["app/models/chat_history.py"])
    assert table_mentioned("addresses", ["app/models/address.py"])
    assert table_mentioned("project_files", ["", "projectFiles テーブルの定義"])
    assert not table_mentioned("documents", ["app/models/user.py"])


def test_table_coverage_without_and_with_schema_files() -> None:
    plain = [UnitFileRef(ROUTE, "module"), UnitFileRef("Dockerfile", "config")]
    schema = [
        *plain,
        UnitFileRef("app/models/user.py", "module", "利用者"),
        UnitFileRef("alembic/versions/0002.py", "config", "projects テーブルを作る"),
    ]

    none = table_coverage(["users", "projects", "documents"], plain)
    some = table_coverage(["users", "projects", "documents", " "], schema)

    assert none == TableCoverage((), ("users", "projects", "documents"))
    assert some.schema_files == ("app/models/user.py", "alembic/versions/0002.py")
    assert some.uncovered == ("documents",)


def test_bundled_schema_file_covers_every_table() -> None:
    """テーブルの名前の出ないスキーマのファイル(`orm.py`)は、全テーブルをまとめて作るとみなす。"""
    bundled = [UnitFileRef("app/models/orm.py", "module", "ORM モデルを定義する")]

    assert table_coverage(["projects", "documents"], bundled) == TableCoverage(
        ("app/models/orm.py",), ()
    )


def test_migration_only_in_plan_config_files_covers_every_table() -> None:
    """モジュール一覧にモデルの行が無くても、段階7 の config_files のマイグレーションで
    テーブルを作る。"""
    unit = _unit("M-01-T01", modules=[ROUTE], config=["alembic/versions/"])

    files = unit_files(unit, None, _row_of)

    assert table_coverage(["users", "projects"], files) == TableCoverage(
        ("alembic/versions/",), ()
    )


def _doc_with_tests(*viewpoints: str, gwt: list[str] | None = None) -> UnitProcedure:
    tests = [Point(viewpoint=v, sut="POST /api/v1/x") for v in viewpoints]
    return UnitProcedure(unit_id="M-01-T01", tests=tests, gwt=gwt or [])


@pytest.mark.parametrize(
    ("statuses", "doc", "expected"),
    [
        (["400", "404"], _doc_with_tests("登録できる"), ("400", "404")),
        (["400", "404"], _doc_with_tests("登録できる", "他人の ID で 404"), ()),
        (["409"], _doc_with_tests("登録できる", gwt=["Then 409 が返る"]), ()),
        (["409"], _doc_with_tests("登録できる", "重複登録はエラーになる"), ()),
        (["409"], _doc_with_tests("登録できる", gwt=["Then エラーなく保存される"]), ("409",)),
        ([], _doc_with_tests("登録できる"), ()),
    ],
)
def test_error_test_gap(statuses: list[str], doc: UnitProcedure, expected: tuple) -> None:
    """エラー欄のステータスが観点・SUT・GWT のどこにも出ず、観点の文に異常系の言葉も無ければ、
    そのステータスすべて(GWT の言葉は見ない)。"""
    assert error_test_gap(statuses, doc) == expected
