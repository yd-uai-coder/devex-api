from tests.fixtures.uml import INTERNAL_DESIGN_MD

from app.uml.generation import (
    DFD_SECTION_TITLE,
    extract_dfd_subjects,
    extract_er_table_blocks,
    extract_er_tables,
    extract_section,
    remove_subsection,
)
from app.uml.generation.sections import find_dfd_heading_end, find_section_heading_end


def test_extract_section_stops_before_next_level_two_heading() -> None:
    section = extract_section(INTERNAL_DESIGN_MD, "3.2")

    assert section.startswith("## 3.2 データモデル定義")
    assert "### テーブル: reservations" in section
    assert "## 3.3" not in section


def test_extract_section_returns_empty_string_when_missing() -> None:
    assert extract_section(INTERNAL_DESIGN_MD, "9.9") == ""


def test_extract_section_runs_to_end_for_last_section() -> None:
    assert extract_section(INTERNAL_DESIGN_MD, "3.4").endswith("共通エラーレスポンス形式")


def test_remove_subsection_drops_dfd_subsection_only() -> None:
    section = remove_subsection(extract_section(INTERNAL_DESIGN_MD, "3.3"), DFD_SECTION_TITLE)

    assert "/api/v1/reservations | 予約作成" in section
    assert "DF-1" not in section


def test_extract_dfd_subjects_lists_headings_with_their_bodies() -> None:
    subjects = extract_dfd_subjects(INTERNAL_DESIGN_MD)

    assert [(s.code, s.title) for s in subjects] == [
        ("DF-1", "POST /api/v1/reservations"),
        ("DF-2", "GET /api/v1/reservations"),
        ("DF-3", "予約リマインドバッチ"),
    ]
    assert "予約リクエスト" in subjects[0].body
    # 本文は次のDF見出しの直前で終わる
    assert "予約一覧" not in subjects[0].body
    # 最後のDF節の本文は、次の##見出し(3.4)の直前で終わる
    assert "共通エラー" not in subjects[2].body


def test_extract_dfd_subjects_is_empty_for_old_format_document() -> None:
    old_format = "# 3. 内部設計書\n\n## 3.3 バックエンド処理・モジュール設計\n- API一覧のみ\n"

    assert extract_dfd_subjects(old_format) == []


def test_extract_er_tables_lists_table_headings_in_section_3_2() -> None:
    assert extract_er_tables(INTERNAL_DESIGN_MD) == ["users", "reservations"]


def test_extract_er_table_blocks_keeps_only_selected_tables() -> None:
    blocks = extract_er_table_blocks(INTERNAL_DESIGN_MD, ["reservations"])

    assert "### テーブル: reservations" in blocks
    assert "user_id" in blocks
    assert "### テーブル: users" not in blocks


def test_find_section_heading_end_points_just_after_heading_line() -> None:
    index = find_section_heading_end(INTERNAL_DESIGN_MD, "3.2")

    assert index is not None
    assert INTERNAL_DESIGN_MD[:index].endswith("## 3.2 データモデル定義\n")
    assert find_section_heading_end(INTERNAL_DESIGN_MD, "9.9") is None


def test_find_dfd_heading_end_matches_by_title_not_code() -> None:
    index = find_dfd_heading_end(INTERNAL_DESIGN_MD, "予約リマインドバッチ")

    assert index is not None
    assert INTERNAL_DESIGN_MD[:index].endswith("#### DF-3: 予約リマインドバッチ\n")
    assert find_dfd_heading_end(INTERNAL_DESIGN_MD, "DF-3") is None
