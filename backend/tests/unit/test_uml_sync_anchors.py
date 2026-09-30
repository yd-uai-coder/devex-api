from tests.fixtures.uml import INTERNAL_DESIGN_MD

from app.uml.sync import (
    APPENDIX_HEADING,
    ImageLink,
    find_block,
    parse_anchors,
    render_block,
    upsert_block,
    with_image_links,
)

_ID_A = "11111111-1111-1111-1111-111111111111"
_ID_B = "22222222-2222-2222-2222-222222222222"


def test_render_block_then_parse_round_trips() -> None:
    block = render_block(_ID_A, 3, "| a |\n|---|\n| 1 |")

    [parsed] = parse_anchors(f"前文\n\n{block}\n\n後文")

    assert parsed.diagram_id == _ID_A
    assert parsed.version == 3
    assert parsed.body == "| a |\n|---|\n| 1 |"


def test_parse_ignores_start_without_end() -> None:
    markdown = f"<!-- uml:diagram:{_ID_A}:start v=1 -->\n本文だけ"

    assert parse_anchors(markdown) == []


def test_component_block_goes_right_under_section_3_3() -> None:
    result = upsert_block(
        INTERNAL_DESIGN_MD,
        diagram_id=_ID_A,
        version=1,
        body="構成表",
        notation="component",
        subject="",
    )

    section_3_3 = result.index("## 3.3 バックエンド処理")
    block = find_block(result, _ID_A)
    assert block is not None
    assert result[section_3_3 : block.start].count("\n") == 2  # 見出し行 + 空行の直後
    assert "- 主要処理ロジック" in result[block.end :]


def test_dfd_block_goes_under_the_matching_df_heading() -> None:
    result = upsert_block(
        INTERNAL_DESIGN_MD,
        diagram_id=_ID_A,
        version=1,
        body="DF表",
        notation="dfd",
        subject="GET /api/v1/reservations",
    )

    block = find_block(result, _ID_A)
    assert block is not None
    heading = result.index("#### DF-2: GET /api/v1/reservations")
    next_heading = result.index("#### DF-3")
    assert heading < block.start < next_heading


def test_second_block_in_same_section_follows_the_first() -> None:
    once = upsert_block(
        INTERNAL_DESIGN_MD, diagram_id=_ID_A, version=1, body="全体", notation="er", subject=""
    )
    twice = upsert_block(
        once, diagram_id=_ID_B, version=1, body="部分", notation="er", subject="予約"
    )

    ids = [b.diagram_id for b in parse_anchors(twice)]
    assert ids == [_ID_A, _ID_B]
    assert twice.index("### テーブル: users") > find_block(twice, _ID_B).end  # type: ignore[union-attr]


def test_existing_block_is_replaced_in_place_and_upsert_is_idempotent() -> None:
    first = upsert_block(
        INTERNAL_DESIGN_MD, diagram_id=_ID_A, version=1, body="旧", notation="er", subject=""
    )
    second = upsert_block(first, diagram_id=_ID_A, version=2, body="新", notation="er", subject="")
    third = upsert_block(second, diagram_id=_ID_A, version=2, body="新", notation="er", subject="")

    [block] = parse_anchors(second)
    assert (block.version, block.body) == (2, "新")
    assert "旧" not in second
    assert third == second


def test_missing_heading_falls_back_to_appendix_section() -> None:
    markdown = "# 3. 内部設計書\n\n## 3.1 技術スタック\n- FastAPI\n"

    once = upsert_block(
        markdown, diagram_id=_ID_A, version=1, body="A", notation="component", subject=""
    )
    twice = upsert_block(
        once, diagram_id=_ID_B, version=1, body="B", notation="dfd", subject="POST /x"
    )

    assert twice.count(APPENDIX_HEADING) == 1
    assert twice.index(APPENDIX_HEADING) < find_block(twice, _ID_A).start  # type: ignore[union-attr]
    assert [b.diagram_id for b in parse_anchors(twice)] == [_ID_A, _ID_B]


def test_with_image_links_puts_image_at_the_top_of_linked_blocks_only() -> None:
    markdown = upsert_block(
        INTERNAL_DESIGN_MD, diagram_id=_ID_A, version=1, body="表A", notation="er", subject=""
    )
    markdown = upsert_block(
        markdown, diagram_id=_ID_B, version=1, body="表B", notation="component", subject=""
    )

    result = with_image_links(markdown, {_ID_A: ImageLink("ER図(全体)", "diagrams/er.svg")})

    block_a = find_block(result, _ID_A)
    block_b = find_block(result, _ID_B)
    assert block_a is not None and block_b is not None
    assert block_a.body == "![ER図(全体)](<diagrams/er.svg>)\n\n表A"
    assert block_b.body == "表B"
