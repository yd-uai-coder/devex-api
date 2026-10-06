from tests.fixtures.uml import INTERNAL_DESIGN_MD

from app.uml.generation import extract_section


def test_extract_section_stops_before_next_level_two_heading() -> None:
    section = extract_section(INTERNAL_DESIGN_MD, "3.2")

    assert section.startswith("## 3.2 データモデル定義")
    assert "### テーブル: reservations" in section
    assert "## 3.3" not in section


def test_extract_section_returns_empty_string_when_missing() -> None:
    assert extract_section(INTERNAL_DESIGN_MD, "9.9") == ""


def test_extract_section_runs_to_end_for_last_section() -> None:
    assert extract_section(INTERNAL_DESIGN_MD, "3.4").endswith("共通エラーレスポンス形式")
