"""段階4 ソフトウェア構造の組み立て(モジュール一覧)と検証のテスト。

SUT: merge_modules / component_layers / path_variants / module_ref_matches
     (app/detailed_design/structure.py)、
     validate_structure / STAGE_VALIDATORS / ComponentDiagramSummary
     (app/detailed_design/validation.py)、パッケージの re-export(app/detailed_design/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。構成図は DB から読まず、
意味モデルの dict と`StageSources`の要約を渡す(読み取りはサービス層の責務)。
"""

import pytest
from tests.fixtures.detailed_design import (
    component_model,
    function_list_model,
    module_list_model,
)

from app.detailed_design import (
    STAGE_VALIDATORS,
    STRUCTURE_STAGE,
    STRUCTURE_SUBJECT,
    ComponentDiagramSummary,
    FunctionListModel,
    ModuleDraft,
    ModuleListModel,
    ModuleRow,
    StageSources,
    component_layers,
    has_errors,
    merge_modules,
    module_ref_matches,
    path_variants,
    validate_stage,
)
from app.detailed_design.validation import validate_structure

PATH = "app/api/routes/reservations.py"


def _function_list() -> FunctionListModel:
    return FunctionListModel.model_validate(
        {
            "groups": ["予約"],
            "functions": [
                {"id": "F-01", "name": "予約を登録する", "group": "予約"},
                {"id": "F-02", "name": "予約を一覧する", "group": "予約"},
            ],
            "next_number": 3,
        }
    )


def _sources(*, status: str = "approved", layers: tuple[str, ...] = ("api", "service")):
    return StageSources(
        stages={1: function_list_model()},
        component_diagram=ComponentDiagramSummary(
            status=status, generation_status="completed", layers=layers
        ),
    )


def _codes(issues) -> list[str]:
    return [issue.code for issue in issues]


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_valid_structure_has_no_issues():
    assert STRUCTURE_STAGE == 4
    assert STRUCTURE_SUBJECT == ""
    assert STAGE_VALIDATORS[STRUCTURE_STAGE] is validate_structure
    assert validate_stage(STRUCTURE_STAGE, module_list_model(), _sources()) == []


# --- component_layers ---


def test_component_layers_in_first_appearance_order_without_blanks():
    model = component_model(layers=("service", "api", "service"))
    model["elements"].append({"id": "x", "name": "x", "kind": "module", "layer": " "})
    model["elements"].append({"id": "y", "name": "y", "kind": "module"})
    assert component_layers(model) == ["service", "api"]
    assert component_layers(None) == []


# --- path_variants / module_ref_matches ---


def test_path_variants_split_units_drop_extension_and_expand_braces():
    assert path_variants(" /app/services/auth.py/ ") == [("app", "services", "auth")]
    assert path_variants("app/repositories/{project, user}.py") == [
        ("app", "repositories", "project"),
        ("app", "repositories", "user"),
    ]
    assert path_variants("app/models/*.py") == [("app", "models", "*")]
    assert path_variants(" ") == []


@pytest.mark.parametrize(
    ("ref", "path", "expected"),
    [
        ("app/services", "app/services/x_client.py", True),  # ディレクトリ
        ("services/auth", "app/services/auth.py", True),  # 先頭を省いた短い書き方
        ("app/services/auth.py", "app/services/auth.py", True),  # 完全一致
        ("app/routers/", "app/routers/web.py", True),  # 末尾の「/」
        ("app/repositories/user", "app/repositories/{project,user}.py", True),
        ("app/models", "app/models/*.py", True),
        ("app/ser", "app/services/x_client.py", False),  # 区切りの途中では一致しない
        ("app/models", "app/models_old.py", False),
        ("app/services/x", "app/services/x_client.py", False),
        ("app/services/auth/token", "app/services/auth.py", False),  # 依存先のほうが長い
    ],
)
def test_module_ref_matches_by_units(ref, path, expected):
    assert module_ref_matches(ref, path) is expected


def test_directory_dependencies_are_not_warned():
    """ディレクトリを依存先に書いても、配下のモジュールがあれば
    警告しない。"""
    model = module_list_model()
    model["modules"][0]["depends_on"] = ["app/services", "app/api/routes", "app/models"]
    model["modules"].append(
        {"path": "app/services/x_client.py", "layer": "service", "responsibility": "x"}
    )
    issues = validate_structure(model, _sources())
    assert [(i.code, i.message) for i in issues] == [
        (
            "UNKNOWN_DEPENDENCY",
            f"{PATH} の依存先「app/models」に当たるモジュールが、モジュール一覧にありません。",
        )
    ]


# --- merge_modules ---


def test_merge_modules_drops_blank_and_duplicate_paths():
    drafts = [
        ModuleDraft(path=" app/a.py ", layer="api", responsibility="A"),
        ModuleDraft(path="app/a.py", layer="service", responsibility="二つ目"),
        ModuleDraft(path=" ", layer="api", responsibility="空"),
    ]
    model = merge_modules(drafts, _function_list())
    assert [row.path for row in model.modules] == ["app/a.py"]
    assert model.modules[0].responsibility == "A"


def test_merge_modules_orders_functions_by_function_list_and_drops_unknown():
    drafts = [
        ModuleDraft(
            path="app/a.py",
            layer="api",
            responsibility="A",
            functions=("F-02", "F-09", "F-01", "F-02"),
            depends_on=(" app/b.py", "", "app/b.py", "langchain"),
            all_functions=True,
        )
    ]
    row = merge_modules(drafts, _function_list()).modules[0]
    assert row == ModuleRow(
        path="app/a.py",
        layer="api",
        responsibility="A",
        depends_on=["app/b.py", "langchain"],
        functions=["F-01", "F-02"],
        all_functions=True,
    )


# --- validate_structure ---


def test_invalid_shape_is_error():
    issues = validate_structure({"modules": "x"}, _sources())
    assert _codes(issues) == ["INVALID_MODEL"]


def test_empty_modules_is_error():
    issues = validate_structure(ModuleListModel().model_dump(), _sources())
    assert "EMPTY_MODULES" in _codes(issues)
    assert has_errors(issues)


def test_component_states():
    model = module_list_model()
    assert "COMPONENT_MISSING" in _codes(
        validate_structure(model, StageSources(stages={1: function_list_model()}))
    )
    generating = StageSources(
        stages={1: function_list_model()},
        component_diagram=ComponentDiagramSummary(status="draft", generation_status="generating"),
    )
    assert "COMPONENT_GENERATING" in _codes(validate_structure(model, generating))
    assert _codes(validate_structure(model, _sources(status="draft"))) == [
        "COMPONENT_NOT_APPROVED"
    ]
    # 出力済み(exported)も承認済みとして扱う(段階2・3の図と同じ)
    assert validate_structure(model, _sources(status="exported")) == []


def test_duplicate_and_empty_paths_are_errors():
    model = module_list_model()
    model["modules"].append(dict(model["modules"][0]))
    model["modules"].append({"path": " ", "layer": "api", "responsibility": "x"})
    codes = _codes(validate_structure(model, _sources()))
    assert codes.count("DUPLICATE_PATH") == 1
    assert "EMPTY_PATH" in codes


def test_unknown_function_is_error():
    issues = validate_structure(module_list_model(functions=["F-01", "F-99"]), _sources())
    assert [(i.severity, i.code) for i in issues] == [("error", "UNKNOWN_FUNCTION")]


def test_warnings_do_not_block():
    model = module_list_model(layer="domain")
    model["modules"][0]["responsibility"] = " "
    model["modules"][0]["depends_on"] = ["app/services/missing.py", "sqlalchemy"]
    issues = validate_structure(model, _sources())
    assert sorted(_codes(issues)) == ["EMPTY_RESPONSIBILITY", "UNKNOWN_DEPENDENCY", "UNKNOWN_LAYER"]
    assert not has_errors(issues)


def test_all_functions_rows_do_not_count_as_coverage():
    model = module_list_model(functions=[])
    model["modules"][0]["all_functions"] = True
    issues = validate_structure(model, _sources())
    assert [(i.code, i.target) for i in issues] == [("UNCOVERED_FUNCTION", "F-01")]
    assert not has_errors(issues)
