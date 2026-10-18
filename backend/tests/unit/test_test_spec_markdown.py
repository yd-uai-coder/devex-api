"""テスト仕様書の md の組み立てのテスト。

SUT は`app/documents/test_spec/markdown.py`の`render_test_spec`・`trace_rows`・`expected_cell`、
ドライバはこのテスト。スタブ不要 ── 対象が純粋関数で、model だけから決まるため。
"""

from tests.fixtures.spec_documents import spec_draft, spec_external, spec_requirements

from app.documents.test_spec import TestSpecModel, build_model, render_test_spec
from app.documents.test_spec.markdown import UNIT_TEST_NOTE, expected_cell, trace_rows


def _model() -> TestSpecModel:
    return build_model(
        spec_draft(),
        requirements=spec_requirements(),
        external=spec_external(),
        links={},
        inputs={},
    )


def test_sections_in_order():
    md = render_test_spec(_model())
    headings = [line for line in md.splitlines() if line.startswith("#")]
    assert headings == [
        "# テスト仕様書(結合・総合)",
        "## 1 テストの方針",
        "## 2 結合テスト(API)",
        "## 3 総合テスト(画面の流れ・非機能)",
        "## 4 要件との対応",
    ]
    # 見出しのすぐ下に、単体テストは実装手順書で見る旨の注記
    assert md.splitlines()[:3] == ["# テスト仕様書(結合・総合)", "", UNIT_TEST_NOTE]


def test_integration_row_and_design_gap():
    model = _model()
    md = render_test_spec(model)
    assert "| IT-001 | POST /api/v1/reservations | 正常 | — | 日付を送る | 201 | FR-001 | — |" in md
    assert expected_cell(model.integration[2]) == "200(**設計に無い**: 並び順が未定)"
    assert "「設計に無い」と印を付けたテストが 1 件あります" in md


def test_trace_lists_must_first_and_marks_untested():
    model = _model()
    assert trace_rows(model) == [
        ("FR-001", "Must", ["IT-001", "IT-002", "IT-003", "ST-001"]),
        ("FR-002", "Must", []),
    ]
    assert "| FR-002 | Must | **テストが無い** |" in render_test_spec(model)


def test_empty_model_renders_none_rows():
    md = render_test_spec(TestSpecModel())
    assert md.count("(なし)") == 3
    assert "設計に無い" not in md
