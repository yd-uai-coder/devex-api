"""外部設計書の model・組み立て・md・検証と、上流の文書で共有する採番・指摘の型(純粋関数)。

SUT: app/documents/external_design の各関数、app/documents/ids・issues。ドライバ: このテスト。
スタブ不要 ── 対象が純粋(副作用なし)で、LLM・DB を呼ばないため。要件定義書の model は
RequirementRow を直接作って渡す。
"""

from app.detailed_design.api_list import extract_api_endpoints
from app.documents.external_design import (
    NO_BODY,
    ApiDraft,
    ApiRow,
    ExternalDesignDraft,
    ExternalDesignModel,
    IntegrationDraft,
    ScreenDraft,
    ScreenRow,
    build_model,
    covered_by,
    render_external_design,
    row_counts,
    screen_id,
    split_ids,
    validate_external_design,
)
from app.documents.ids import carry_over_ids, initial_next_seq, parse_seq_id, seq_id
from app.documents.issues import DocumentIssue
from app.documents.requirements import RequirementRow, RequirementsModel


def _requirements() -> RequirementsModel:
    rows = [
        RequirementRow(id="FR-001", kind="FR", priority="Must", text="予約", source="user"),
        RequirementRow(id="FR-002", kind="FR", priority="Must", text="一覧", source="user"),
        RequirementRow(id="FR-003", kind="FR", priority="Could", text="通知", source="assumed"),
        RequirementRow(id="NFR-001", kind="NFR", category="性能", text="速い", source="user"),
    ]
    return RequirementsModel(purpose="目的", rows=rows)


def _draft(**kwargs) -> ExternalDesignDraft:
    values = {
        "overview": "ブラウザ → API → DB",
        "transition": "SCR-001 → SCR-002",
        "data_io": "CSV は扱わない",
        "screens": (
            ScreenDraft("SCR-001", "予約画面", "予約する", "Must", ("FR-001",), "入力: 日付"),
            ScreenDraft("SCR-002", "一覧画面", "一覧を見る", "Should", ("FR-002",), ""),
        ),
        "apis": (
            ApiDraft(
                "post", "/api/v1/reservations", "予約を登録", ("SCR-001",), ("FR-001",),
                "date", "201 予約", "409 重複",
            ),
            ApiDraft("GET", "/api/v1/reservations", "一覧", ("SCR-002",), ("FR-002",)),
        ),
        "integrations": (
            IntegrationDraft("Gemini API", "生成", "API キー", "生成時", ("NFR-001",)),
        ),
    }
    values.update(kwargs)
    return ExternalDesignDraft(**values)  # type: ignore[arg-type]


# ---- 共有の採番・指摘の型 ----


def test_seq_id_round_trip_and_carry_over() -> None:
    assert seq_id("SCR", 3) == "SCR-003"
    assert parse_seq_id("SCR-003", ("SCR",)) == ("SCR", 3)
    assert parse_seq_id("SCR003", ("SCR",)) is None
    next_seq = initial_next_seq(("SCR",), ["SCR-001", "SCR-004"], {"SCR": 6})
    assert next_seq == {"SCR": 6}

    ids, after = carry_over_ids(
        [("SCR", "scr-004"), ("SCR", "SCR-004"), ("SCR", "SCR-002")],
        {"SCR-001": "SCR", "SCR-004": "SCR"},
        next_seq,
    )

    # 前の版に在る ID だけ引き継ぎ、重複・前の版に無い ID は次の番号(消えた番号は使わない)
    assert ids == ["SCR-004", "SCR-006", "SCR-007"]
    assert after == {"SCR": 8}
    assert next_seq == {"SCR": 6}  # 渡した辞書は書き換えない


def test_document_issue_defaults_to_no_targets() -> None:
    assert DocumentIssue("minor", "X", "m").targets == ()


# ---- 組み立て ----


def test_split_ids_normalizes_and_splits_joined_cells() -> None:
    assert split_ids(["fr-001・FR-002", "FR-001", "—", " `NFR-001` "]) == [
        "FR-001",
        "FR-002",
        "NFR-001",
    ]


def test_build_model_numbers_screens_and_copies_requirement_ids() -> None:
    model = build_model(_draft(), None, _requirements())

    assert [s.id for s in model.screens] == ["SCR-001", "SCR-002"]
    assert model.screens[1].priority == "Should"
    assert model.next_seq == {"SCR": 3}
    assert model.apis[0].method == "POST"
    assert model.apis[0].screens == ["SCR-001"]
    assert model.known_req_ids == ["FR-001", "FR-002", "FR-003", "NFR-001"]
    assert model.must_fr_ids == ["FR-001", "FR-002"]
    assert row_counts(model) == {"screens": 2, "apis": 2, "integrations": 1}


def test_build_model_carries_over_screen_ids_and_remaps_api_refs() -> None:
    previous = ExternalDesignModel(
        screens=[ScreenRow(id="SCR-001", name="予約画面"), ScreenRow(id="SCR-002", name="旧画面")],
        next_seq={"SCR": 3},
    )
    draft = _draft(
        screens=(
            ScreenDraft("SCR-009", "新しい画面"),  # 前の版に無い仮の ID → 次の番号
            ScreenDraft("SCR-001", "予約画面"),  # 前の版と同じ画面 → 引き継ぐ
        ),
        apis=(ApiDraft("GET", "/api/v1/x", "x", ("SCR-009", "SCR-001", "SCR-404")),),
    )

    model = build_model(draft, previous, None)

    assert [s.id for s in model.screens] == ["SCR-003", "SCR-001"]
    # 仮の ID は振り直した ID に付け替え、知らない ID はそのまま(検証で指摘する)
    assert model.apis[0].screens == ["SCR-003", "SCR-001", "SCR-404"]
    assert model.next_seq == {"SCR": 4}
    assert model.known_req_ids == []


def test_build_model_drops_malformed_and_duplicate_apis() -> None:
    draft = _draft(
        apis=(
            ApiDraft("GET", "/api/v1/items/{id}", "1件"),
            ApiDraft("get", "/api/v1/items/{item_id}/", "同じ API"),
            ApiDraft("FETCH", "/api/v1/x"),
            ApiDraft("GET", "api/v1/x"),
            ApiDraft("POST", "`/api/v1/items`", body="—"),
        ),
        screens=(ScreenDraft("SCR-001", " "),),
    )

    model = build_model(draft, None, None)

    assert [a.label for a in model.apis] == ["GET /api/v1/items/{id}", "POST /api/v1/items"]
    assert model.apis[1].body == ""
    assert model.screens == []


# ---- md ----


def test_render_keeps_headings_and_api_list_parsable() -> None:
    model = build_model(_draft(), None, _requirements())

    md = render_external_design(model)

    for heading in (
        "# 2. 外部設計書",
        "## 2.1 システム構成概要",
        "## 2.2 画面一覧・画面遷移フロー（概要）",
        "## 2.3 主要画面のUI/UX仕様",
        "## 2.4 外部システム・API連携仕様",
        "## 2.5 データ入出力仕様",
        "## 2.6 API一覧",
    ):
        assert heading in md
    assert "| 画面ID | 画面名 | 主要な役割 | 優先度 | 要件 |" in md
    assert "### SCR-001: 予約画面" in md
    assert "### SCR-002" not in md  # UI 仕様の無い画面は小見出しを立てない
    assert "```text\nSCR-001 → SCR-002\n```" in md
    assert "| Gemini API | 生成 | API キー | 生成時 | NFR-001 |" in md
    # 段階1・簡易モードの段階8が使う 2.6 の解析で、今までどおり読める
    endpoints = extract_api_endpoints(md)
    assert [e.key for e in endpoints] == ["POST /api/v1/reservations", "GET /api/v1/reservations"]
    assert endpoints[0].screens == ("SCR-001",)
    assert "| FR-001 | SCR-001 | POST /api/v1/reservations |" in md


def test_render_keeps_a_fenced_diagram_as_is_and_handles_empty() -> None:
    md = render_external_design(ExternalDesignModel(transition="```\nA → B\n```"))

    assert "```text" not in md
    assert md.count("(なし)") == 4  # 画面・UI 仕様・外部連携・API
    assert "(要件定義書の Must の機能要件がありません)" in md


# ---- 検証 ----


def _codes(model: ExternalDesignModel) -> list[str]:
    return [i.code for i in validate_external_design(model)]


def test_validate_clean_model_has_no_issues() -> None:
    assert validate_external_design(build_model(_draft(), None, _requirements())) == []


def test_validate_reports_each_issue() -> None:
    model = ExternalDesignModel(
        screens=[ScreenRow(id="SCR-001", name="画面")],
        apis=[
            ApiRow(method="POST", path="/a", req_ids=["FR-001", "FR-099"]),
            ApiRow(method="GET", path="/b", screens=["SCR-404"]),
        ],
        known_req_ids=["FR-001", "FR-002"],
        must_fr_ids=["FR-001", "FR-002"],
    )

    issues = {i.code: i for i in validate_external_design(model)}

    assert list(issues) == [
        "MUST_REQUIREMENT_UNCOVERED",
        "API_BODY_MISSING",
        "UNKNOWN_REQUIREMENT_ID",
        "UNLINKED_ROWS",
        "UNKNOWN_SCREEN_REF",
    ]
    assert issues["MUST_REQUIREMENT_UNCOVERED"].targets == ("FR-002",)
    assert issues["MUST_REQUIREMENT_UNCOVERED"].level == "major"
    assert issues["API_BODY_MISSING"].targets == ("POST /a",)
    assert issues["UNKNOWN_REQUIREMENT_ID"].targets == ("FR-099",)
    assert issues["UNLINKED_ROWS"].targets == ("SCR-001", "GET /b")
    assert issues["UNKNOWN_SCREEN_REF"].targets == ("SCR-404",)
    assert covered_by(model, "FR-001") == ([], ["POST /a"])


def test_validate_accepts_no_body_marker() -> None:
    """本文の要らない POST は、本文の欄に「なし」と書けば警告しない(空欄だけを警告する)。"""
    model = ExternalDesignModel(
        apis=[
            ApiRow(method="POST", path="/generate", body=NO_BODY),
            ApiRow(method="PUT", path="/a/{id}"),
        ]
    )

    [issue] = [i for i in validate_external_design(model) if i.code == "API_BODY_MISSING"]
    assert issue.targets == ("PUT /a/{id}",)


def test_validate_skips_requirement_checks_without_requirements_copy() -> None:
    model = ExternalDesignModel(apis=[ApiRow(method="GET", path="/b", req_ids=["FR-099"])])

    assert _codes(model) == []
    assert screen_id(12) == "SCR-012"
