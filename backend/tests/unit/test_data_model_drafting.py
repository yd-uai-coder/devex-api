"""段階3の下書きの入出力(プロンプト・出力スキーマ・意味モデルへの写像)のテスト。

SUT: build_er_messages / to_er_model / build_crud_messages / to_crud_drafts / data_store_names
     (app/detailed_design/data_model_drafting.py)、E2E 用の固定の出力(app/ai/llm/fake.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、LLM を呼ばないため(メッセージを組み立て、
構造化出力を受け取って変換するだけ。LLM の呼び出しはサービス層の責務)。
"""

from app.ai.llm.fake import _UML_OUTPUTS
from app.detailed_design import (
    CrudDraft,
    DfdAccess,
    FunctionListModel,
    ProcessSummaryRow,
    merge_crud,
)
from app.detailed_design.data_model_drafting import (
    CRUD_SYSTEM_PROMPT,
    ER_SYSTEM_PROMPT,
    CrudGenerationOutput,
    DataModelErOutput,
    DraftedColumn,
    DraftedTable,
    GeneratedCrudCell,
    build_crud_messages,
    build_er_messages,
    data_store_names,
    to_crud_drafts,
    to_er_model,
)
from app.uml.generation.prompts import ExistingDataItem
from app.uml.generation.schemas import GeneratedTableRelation
from app.uml.validation import validate_diagram

_FUNCTIONS = FunctionListModel.model_validate(
    {
        "groups": ["予約"],
        "functions": [
            {"id": "F-01", "name": "予約を登録する", "group": "予約", "summary": "保存する"},
            {"id": "F-02", "name": "予約を一覧する", "group": "予約"},
        ],
        "next_number": 3,
    }
)
_SUMMARIES = [ProcessSummaryRow(function_id="F-01", input="予約", process="保存", output="予約")]
_ACCESSES = [
    DfdAccess("F-01", "reservations", "write"),
    DfdAccess("F-02", "reservations", "read"),
]


def _column(name: str, *, pk: bool = False) -> DraftedColumn:
    return DraftedColumn(
        name=name, type="UUID", is_primary_key=pk, is_foreign_key=not pk, nullable=False
    )


def test_er_and_crud_drafts_merge_into_crud_model() -> None:
    """統合スモーク: ER の下書きのテーブル名と CRUD の下書きから、CRUD 図を組み立てられる。"""
    er = to_er_model(_UML_OUTPUTS[DataModelErOutput])  # type: ignore[arg-type]
    drafts = to_crud_drafts(_UML_OUTPUTS[CrudGenerationOutput])  # type: ignore[arg-type]
    model = merge_crud(drafts, _ACCESSES, _FUNCTIONS, [e.name for e in er.elements])
    assert [(c.function_id, c.ops, c.draft) for c in model.cells] == [
        ("F-01", "C", True),
        ("F-02", "R", False),
    ]
    assert er.elements[0].columns[1].constraints == "INDEX"


def test_build_er_messages_lists_stores_dictionary_and_summaries() -> None:
    stores = data_store_names([*_ACCESSES, DfdAccess("F-01", "items", "read")])
    assert stores == ["items", "reservations"]
    items = [ExistingDataItem(name="予約", field_names=["id", "item_id"])]
    system, human = build_er_messages(stores, items, _SUMMARIES)
    assert system.content == ER_SYSTEM_PROMPT
    assert "- items\n- reservations" in human.content
    assert "- 予約(id, item_id)" in human.content
    assert "- F-01: 入力=予約 / 処理=保存 / 出力=予約" in human.content
    _, empty = build_er_messages([], [], [])
    assert "## データストア\n(ありません)" in empty.content


def test_to_er_model_keeps_notes_and_drops_duplicates_and_dangling_relations() -> None:
    output = DataModelErOutput(
        tables=[
            DraftedTable(
                id="t1",
                name=" users ",
                description="利用者",
                columns=[
                    DraftedColumn(
                        name="email",
                        type="TEXT",
                        is_primary_key=False,
                        is_foreign_key=False,
                        nullable=False,
                        constraints="UNIQUE",
                        description="ログイン ID",
                    ),
                    _column("id", pk=True),
                ],
            ),
            DraftedTable(id="t2", name="users", columns=[_column("id", pk=True)]),  # 同じ名前
            DraftedTable(id="t3", name="projects", columns=[_column("id", pk=True)]),
        ],
        relations=[
            GeneratedTableRelation(
                id="r1", source_id="t1", target_id="t3", relation_type="one_to_many"
            ),
            GeneratedTableRelation(
                id="r2", source_id="t2", target_id="t3", relation_type="one_to_many"
            ),
        ],
    )
    model = to_er_model(output)
    assert [e.name for e in model.elements] == ["users", "projects"]
    assert model.elements[0].description == "利用者"
    assert (model.elements[0].columns[0].constraints, model.elements[0].columns[0].description) == (
        "UNIQUE",
        "ログイン ID",
    )
    assert [r.id for r in model.relations] == ["r1"]
    assert validate_diagram(model).errors == []


def test_build_crud_messages_marks_dfd_accesses_as_fixed() -> None:
    system, human = build_crud_messages(
        _FUNCTIONS.functions, _SUMMARIES, ["reservations"], _ACCESSES
    )
    assert system.content == CRUD_SYSTEM_PROMPT
    assert "- F-01 予約を登録する(機能グループ: 予約)保存する" in human.content
    assert "- F-01 × reservations: 書き込み" in human.content
    assert "- F-02 × reservations: 読み" in human.content
    assert "## テーブル\n- reservations" in human.content


def test_to_crud_drafts() -> None:
    output = CrudGenerationOutput(
        cells=[GeneratedCrudCell(function_id="F-01", table="reservations", ops="CR")]
    )
    assert to_crud_drafts(output) == [CrudDraft("F-01", "reservations", "CR")]
