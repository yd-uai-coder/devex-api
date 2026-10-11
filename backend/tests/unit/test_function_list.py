"""段階1 機能一覧の組み立て(処理IDの採番と引き継ぎ・機能グループの初期値)と検証のテスト。

SUT: merge_draft / initial_group(app/detailed_design/function_list.py)、
     validate_function_list / validate_stage / has_errors / STAGE_VALIDATORS
     (app/detailed_design/validation.py)、パッケージの re-export(app/detailed_design/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。
"""

from tests.fixtures.detailed_design import function_list_model

from app.detailed_design import (
    STAGE_VALIDATORS,
    FunctionDraft,
    FunctionListModel,
    FunctionRow,
    StageIssue,
    StageSources,
    has_errors,
    initial_group,
    merge_draft,
    validate_stage,
)
from app.detailed_design.function_list import FunctionKind
from app.detailed_design.validation import validate_function_list


def _draft(
    name: str, trigger: str, *, kind: FunctionKind = "API", hint: str = ""
) -> FunctionDraft:
    return FunctionDraft(
        name=name, kind=kind, trigger=trigger, screens=(), summary="", group_hint=hint
    )


def test_merge_then_validate_without_errors() -> None:
    """統合スモーク: 下書きを組み立てた結果が、段階1の検証をエラーなしで通る。"""
    model = merge_draft([_draft("作成する", "POST /api/v1/projects")])

    issues = validate_stage(1, model.model_dump(), StageSources())

    assert [f.id for f in model.functions] == ["F-01"]
    assert has_errors(issues) is False


def test_initial_group_uses_resource_name() -> None:
    assert initial_group("POST /api/v1/projects") == "projects"
    assert initial_group("GET /api/v1/projects/{id}") == "projects"
    assert initial_group("GET /api/v1/projects/{id}/documents/{doc_id}/download") == "documents"
    assert initial_group("GET /api/v1/users/me") == "users"
    assert initial_group("GET /health") == "health"
    assert initial_group("夜間の集計", fallback="集計") == "集計"
    assert initial_group("夜間の集計") == "その他"


def test_merge_draft_numbers_new_rows_in_order() -> None:
    model = merge_draft(
        [
            _draft("登録する", "POST /api/v1/auth/register"),
            _draft("作成する", "POST /api/v1/projects"),
            _draft("集計する", "毎日の集計", kind="バッチ", hint="運用"),
            _draft("図を描く", "SCR-007: 図の描画", kind="画面", hint="UML図"),
        ]
    )

    assert [(f.id, f.group_initial, f.group) for f in model.functions] == [
        ("F-01", "auth", "auth"),
        ("F-02", "projects", "projects"),
        ("F-03", "運用", "運用"),
        ("F-04", "UML図", "UML図"),  # 画面内の処理も、機能グループは AI の提案
    ]
    assert model.groups == ["auth", "projects", "運用", "UML図"]
    assert model.next_number == 5


def test_regeneration_keeps_ids_and_confirmed_groups() -> None:
    previous = FunctionListModel(
        groups=["文書", "projects"],
        functions=[
            FunctionRow(id="F-01", name="作成する", trigger="POST /api/v1/projects",
                        group_initial="projects", group="projects"),
            FunctionRow(id="F-02", name="生成する", trigger="POST /api/v1/projects/{id}/generate",
                        group_initial="generate", group="文書"),
            FunctionRow(id="F-03", name="消す", trigger="DELETE /api/v1/projects/{id}",
                        group_initial="projects", group="projects"),
        ],
        next_number=4,
    )

    model = merge_draft(
        [
            _draft("設計書を生成する", "POST /api/v1/projects/{project_id}/generate"),
            _draft("一覧を返す", "GET /api/v1/projects"),
            _draft("作成する", "POST /api/v1/projects"),
        ],
        previous,
    )

    assert [(f.id, f.name, f.group) for f in model.functions] == [
        ("F-02", "設計書を生成する", "文書"),
        ("F-04", "一覧を返す", "projects"),  # 消えた F-03 は再利用しない
        ("F-01", "作成する", "projects"),
    ]
    assert model.groups == ["文書", "projects"]
    assert model.next_number == 5


def test_duplicate_draft_trigger_gets_a_new_id() -> None:
    previous = merge_draft([_draft("作成する", "POST /api/v1/projects")])

    model = merge_draft(
        [_draft("作成する", "POST /api/v1/projects"), _draft("作成2", "POST /api/v1/projects")],
        previous,
    )

    assert [f.id for f in model.functions] == ["F-01", "F-02"]


def test_validate_reports_errors() -> None:
    model = {
        "groups": ["a", "a", "空"],
        "functions": [
            {"id": "F-01", "name": "", "group": "a"},
            {"id": "F-01", "name": "x", "group": "無い"},
            {"id": "X-9", "name": "y", "group": "a"},
            {"id": "F-07", "name": "z", "group": "a"},
        ],
        "next_number": 2,
    }

    issues = validate_function_list(model, StageSources())
    codes = {(i.severity, i.code, i.target) for i in issues}

    assert ("error", "DUPLICATE_GROUP", "a") in codes
    assert ("error", "EMPTY_NAME", "F-01") in codes
    assert ("error", "DUPLICATE_FUNCTION_ID", "F-01") in codes
    assert ("error", "UNKNOWN_GROUP", "F-01") in codes
    assert ("error", "INVALID_FUNCTION_ID", "X-9") in codes
    assert ("error", "FUNCTION_ID_NOT_ISSUED", "F-07") in codes
    assert ("warning", "UNUSED_GROUP", "空") in codes


def test_validate_rejects_malformed_and_empty_models() -> None:
    assert [i.code for i in validate_function_list({"functions": "x"}, StageSources())] == [
        "INVALID_MODEL"
    ]
    assert [i.code for i in validate_function_list({"groups": []}, StageSources())] == [
        "EMPTY_FUNCTIONS"
    ]


def test_validate_warns_duplicate_trigger_and_missing_api() -> None:
    model = function_list_model()
    model["functions"].append({**model["functions"][0], "id": "F-02"})
    model["next_number"] = 3
    external = (
        "## 2.6 API一覧\n| メソッド | パス |\n|---|---|\n"
        "| POST | /api/v1/reservations |\n| GET | /api/v1/items |\n"
    )

    issues = validate_function_list(model, StageSources(documents={"external_design": external}))

    assert has_errors(issues) is False
    assert [(i.code, i.target) for i in issues] == [
        ("DUPLICATE_TRIGGER", "F-01"),
        ("MISSING_API", "GET /api/v1/items"),
        # 外部設計書に model が無い(Phase 37 より前の版)
        ("REQUIREMENT_IDS_MISSING", None),
    ]


def test_stages_without_validator_have_no_issues() -> None:
    # 段階1〜8のすべてに検証がある。登録の無い番号は指摘なし
    assert set(STAGE_VALIDATORS) == {1, 2, 3, 4, 5, 6, 7, 8}
    assert validate_stage(9, {"anything": 1}, StageSources()) == []
    assert validate_stage(1, None, StageSources()) == []
    assert has_errors([StageIssue("warning", "W", "w")]) is False


def test_merge_copies_api_requirements_from_external_design() -> None:
    """API の処理は外部設計書の 2.6 の行の要件ID を写す。API でない処理と、2.6 に無い API は
    下書きの値(空白・重複は除く)。"""
    drafts = [
        FunctionDraft("登録する", "API", "post /api/v1/reservations/", (), "", "", ("FR-009",)),
        FunctionDraft("一覧を返す", "API", "GET /api/v1/items", (), "", "", ("FR-002",)),
        FunctionDraft("集計する", "画面", "SCR-001: 集計", (), "", "集計", (" FR-003", "FR-003")),
    ]
    api_req_ids = {"POST /api/v1/reservations": ["FR-001"]}

    model = merge_draft(drafts, api_req_ids=api_req_ids)

    assert [row.req_ids for row in model.functions] == [["FR-001"], ["FR-002"], ["FR-003"]]


def test_validate_traces_requirements_from_external_design_copy() -> None:
    """要件の追跡: 処理の無い Must の要件・要件の無い処理・要件定義書に無い要件ID(警告)。
    要件ID は外部設計書の model に写したものと照らす。"""
    model = function_list_model()
    model["functions"].append(
        {**model["functions"][0], "id": "F-02", "trigger": "GET /a", "req_ids": []}
    )
    model["functions"].append(
        {**model["functions"][0], "id": "F-03", "trigger": "GET /b", "req_ids": ["FR-099"]}
    )
    model["next_number"] = 4
    external = {"known_req_ids": ["FR-001", "FR-002"], "must_fr_ids": ["FR-001", "FR-002"]}
    sources = StageSources(document_models={"external_design": external})

    issues = validate_function_list(model, sources)

    assert has_errors(issues) is False
    assert [(i.code, i.target) for i in issues] == [
        ("MUST_REQUIREMENT_UNPROCESSED", "FR-002"),
        ("UNLINKED_FUNCTION", "F-02"),
        ("UNKNOWN_REQUIREMENT_ID", "F-03"),
    ]
