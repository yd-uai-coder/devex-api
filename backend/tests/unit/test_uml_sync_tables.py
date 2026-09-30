import uuid

from app.uml.domain import ComponentSemanticModel, DfdSemanticModel, ErSemanticModel
from app.uml.sync import DataItemSummary, render_block_body, render_element_table


def test_component_table_lists_dependencies_per_module() -> None:
    model = ComponentSemanticModel.model_validate(
        {
            "elements": [
                {"id": "c1", "name": "api", "description": "ルーター"},
                {"id": "c2", "name": "service"},
            ],
            "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
        }
    )

    table = render_element_table(model, {})

    assert table.splitlines() == [
        "| 名称 | 種別 | 説明 | 依存先 |",
        "|---|---|---|---|",
        "| api | モジュール | ルーター | service |",
        "| service | モジュール | — | — |",
    ]


def test_er_table_has_columns_and_relations() -> None:
    model = ErSemanticModel.model_validate(
        {
            "elements": [
                {
                    "id": "t1",
                    "name": "users",
                    "columns": [
                        {"name": "id", "type": "UUID", "is_primary_key": True, "nullable": False}
                    ],
                },
                {
                    "id": "t2",
                    "name": "reservations",
                    "columns": [{"name": "user_id", "type": "UUID", "is_foreign_key": True}],
                },
            ],
            "relations": [
                {"id": "r1", "source_id": "t1", "target_id": "t2", "relation_type": "one_to_many"}
            ],
        }
    )

    table = render_element_table(model, {})

    assert "| users | id | UUID | ○ |  | 不可 |" in table
    assert "| reservations | user_id | UUID |  | ○ | 可 |" in table
    assert "| users | reservations | 1対多 |" in table


def test_dfd_table_takes_transform_from_source_process_and_lists_data_items() -> None:
    request = uuid.uuid4()
    model = DfdSemanticModel.model_validate(
        {
            "elements": [
                {"id": "e1", "name": "利用者", "element_type": "external_entity"},
                {
                    "id": "p1",
                    "name": "予約を登録する",
                    "element_type": "process",
                    "description": "検証して保存する",
                },
                {"id": "s1", "name": "reservations", "element_type": "data_store"},
            ],
            "relations": [
                {"id": "f1", "source_id": "e1", "target_id": "p1", "data_item_id": str(request)},
                {"id": "f2", "source_id": "p1", "target_id": "s1", "data_item_id": str(request)},
            ],
        }
    )
    items = {request: DataItemSummary("予約リクエスト", ("item_id", "start_at"))}

    table = render_element_table(model, items)

    assert "| 利用者 | 予約リクエスト | — | 予約を登録する |" in table
    assert "| 予約を登録する | 予約リクエスト | 検証して保存する | reservations |" in table
    assert table.count("- データ項目: 予約リクエスト(item_id, start_at)") == 1


def test_cells_escape_pipes_and_newlines() -> None:
    model = ComponentSemanticModel.model_validate(
        {"elements": [{"id": "c1", "name": "a|b", "description": "1行目\n2行目"}]}
    )

    table = render_element_table(model, {})

    assert "| a\\|b | モジュール | 1行目 2行目 | — |" in table


def test_block_body_starts_with_title_notice() -> None:
    body = render_block_body("ER図(全体)", "| 表 |")

    assert body.startswith("> 図: ER図(全体) ──")
    assert body.endswith("\n\n| 表 |")
