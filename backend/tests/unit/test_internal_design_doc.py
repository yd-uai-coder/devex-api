"""内部設計書の model・組み立て・md・検証・下書きの入出力(純粋関数)。

SUT: app/documents/internal_design の各関数。ドライバ: このテスト。
スタブ不要 ── 対象が純粋(副作用なし)で、LLM・DB を呼ばないため。md が古い版の解析
(`parse_internal_design`)で同じように読めることも、解析を素で呼んで確かめる。
"""

from app.detailed_design.simple_procedure import parse_internal_design
from app.documents.internal_design import (
    ColumnDraft,
    DataFlowDraft,
    DataItemDraft,
    FlowDraft,
    InternalApiDraft,
    InternalDesignDraft,
    ModuleDraft,
    TableDraft,
    body_text,
    build_model,
    dataflow_id,
    dataflow_seq,
    render_internal_design,
    row_counts,
    validate_internal_design,
)
from app.documents.internal_design.drafting import (
    GeneratedDataFlow,
    GeneratedFlow,
    GeneratedInternalApi,
    InternalDesignGenerationOutput,
    build_internal_design_messages,
    to_draft,
)


def _draft(**kwargs) -> InternalDesignDraft:
    values = {
        "architecture": "FastAPI + PostgreSQL",
        "data_model_note": "- reservations: 予約",
        "tables": (
            TableDraft(
                "`reservations`",
                "予約",
                (
                    ColumnDraft("id", "UUID", "PK", "予約ID"),
                    ColumnDraft("date", "DATE", "—", "日付"),
                ),
            ),
            TableDraft("reservations"),
        ),
        "module_policy": "```text\napp/\n  routers/\n```",
        "apis": (
            InternalApiDraft("post", "/api/v1/reservations", "routers が受け、services が登録"),
            InternalApiDraft("GET", "/api/v1/reservations", "一覧"),
            InternalApiDraft("GET", "/api/v1/reservations/", "重複"),
            InternalApiDraft("FETCH", "/x", "形が違う"),
        ),
        "modules": (
            ModuleDraft("app/routers/*.py", "ルート", "HTTP の入口", ("app/services/*.py",)),
            ModuleDraft("app/services/*.py", "サービス", "業務処理", ("なし",)),
        ),
        "dataflows": (
            DataFlowDraft(
                "POST /api/v1/reservations",
                (
                    FlowDraft("利用者", "予約の入力", "", "app/routers/reservations.py"),
                    FlowDraft("app/services/reservation.py", "予約", "保存", "reservations"),
                ),
                (DataItemDraft("予約", ("date", "user_id")),),
            ),
            DataFlowDraft("GET /api/v1/reservations", (FlowDraft("reservations", "予約"),)),
            DataFlowDraft("毎日の集計"),
        ),
        "error_policy": "## エラー\n- 共通の形で返す",
    }
    values.update(kwargs)
    return InternalDesignDraft(**values)  # type: ignore[arg-type]


def test_dataflow_id_round_trip() -> None:
    assert dataflow_id(3) == "DF-3"
    assert dataflow_seq("df-03") == 3
    assert dataflow_seq("F-03") is None


def test_build_model_keeps_well_formed_rows() -> None:
    model = build_model(_draft(), None, ["POST /api/v1/reservations"])

    assert [t.name for t in model.tables] == ["reservations"]
    assert model.tables[0].columns[1].constraints == ""
    assert [a.label for a in model.apis] == [
        "POST /api/v1/reservations",
        "GET /api/v1/reservations",
    ]
    assert model.modules[1].depends_on == []
    assert [(f.id, f.trigger) for f in model.dataflows] == [
        ("DF-1", "POST /api/v1/reservations"),
        ("DF-2", "GET /api/v1/reservations"),
        ("DF-3", "毎日の集計"),
    ]
    assert model.next_dataflow_seq == 4
    assert model.external_apis == ["POST /api/v1/reservations"]


def test_build_model_carries_over_dataflow_ids_by_trigger() -> None:
    previous = build_model(_draft(), None)
    redrafted = _draft(
        dataflows=(
            DataFlowDraft("DELETE /api/v1/reservations/{id}"),
            DataFlowDraft("毎日の集計"),
            DataFlowDraft("POST /api/v1/reservations/"),
            DataFlowDraft("POST /api/v1/reservations"),
        )
    )

    model = build_model(redrafted, previous)

    # 同じ API(末尾の / は同じとみなす)・同じバッチ名は前の ID。消えた DF-2 は使い回さない
    assert [(f.id, f.trigger) for f in model.dataflows] == [
        ("DF-4", "DELETE /api/v1/reservations/{id}"),
        ("DF-3", "毎日の集計"),
        ("DF-1", "POST /api/v1/reservations/"),
    ]
    assert model.next_dataflow_seq == 5


def test_render_is_read_by_the_md_parser() -> None:
    model = build_model(_draft(), None)
    markdown = render_internal_design(model)

    book = parse_internal_design(markdown)

    assert book.has_module_list
    assert list(book.modules) == ["app/routers/*.py", "app/services/*.py"]
    assert book.modules["app/routers/*.py"].depends_on == ["app/services/*.py"]
    assert list(book.dataflows) == ["DF-1", "DF-2", "DF-3"]
    assert book.dataflows["DF-1"].tables == ("reservations",)
    assert "- データ項目: 予約(date, user_id)" in book.dataflows["DF-1"].markdown
    assert book.dataflows["DF-3"].trigger is None
    assert [a.key for a in book.apis.values()] == [
        "POST /api/v1/reservations",
        "GET /api/v1/reservations",
    ]
    assert list(book.tables) == ["reservations"]
    assert "FastAPI" in book.architecture
    # 本文の見出しは太字にするので、3.4 の節が途中で切れない
    assert book.error_policy == "**エラー**\n- 共通の形で返す"


def test_body_text_keeps_headings_inside_code_fences() -> None:
    assert body_text("") == "—"
    assert body_text("# A\n```\n# keep\n```") == "**A**\n```\n# keep\n```"


def test_validation_reports_gaps() -> None:
    model = build_model(
        _draft(modules=(), apis=(InternalApiDraft("GET", "/api/v1/reservations"),)),
        None,
        ["POST /api/v1/reservations", "DELETE /api/v1/reservations/{id}"],
    )

    issues = {i.code: i for i in validate_internal_design(model)}

    assert issues["NO_MODULE_LIST"].level == "critical"
    assert issues["API_WITHOUT_DATAFLOW"].targets == ("DELETE /api/v1/reservations/{id}",)
    assert issues["DATAFLOW_API_UNLISTED"].targets == ("DF-1",)
    assert issues["DATAFLOW_EMPTY"].targets == ("DF-3",)
    assert row_counts(model) == {"tables": 1, "apis": 1, "modules": 0, "dataflows": 3}


def test_validation_is_quiet_for_a_complete_design() -> None:
    model = build_model(
        _draft(dataflows=_draft().dataflows[:2]),
        None,
        ["POST /api/v1/reservations", "GET /api/v1/reservations"],
    )

    assert validate_internal_design(model) == []


def test_drafting_converts_output_and_builds_messages() -> None:
    output = InternalDesignGenerationOutput(
        architecture="a",
        data_model_note="b",
        tables=[],
        module_policy="c",
        apis=[GeneratedInternalApi(method="GET", path="/x", summary="s")],
        modules=[],
        dataflows=[
            GeneratedDataFlow(
                trigger="GET /x",
                flows=[GeneratedFlow(source="利用者", data="d", target="app/x.py")],
                data_items=[],
            )
        ],
        error_policy="e",
    )

    draft = to_draft(output)
    messages = build_internal_design_messages("要件", "外部")

    assert draft.apis == (InternalApiDraft("GET", "/x", "s"),)
    assert draft.dataflows[0].flows == (FlowDraft("利用者", "d", "", "app/x.py"),)
    assert "## 要件定義書\n要件" in messages[1].content
    assert "## 外部設計書\n外部" in messages[1].content
