"""テスト仕様書の model から md を組み立てる(純粋関数)。

章立ては見本(`appendix/design-review-sample/test-spec.md`)と同じ: 1 方針 / 2 結合テスト(API)/
3 総合テスト / 4 要件との対応。設計で決まっていないことは、期待結果の後ろに「**設計に無い**」で
示す。
要件との対応は、Must の機能要件(テストが無ければそう書く)と、テストが指すほかの要件を載せる。
"""

from collections.abc import Sequence

from app.detailed_design.document.markdown import md_table
from app.documents.external_design.markdown import ids_cell
from app.documents.test_spec.model import VIEWPOINTS, SpecCase, TestSpecModel

# 扱うのは結合・総合テストだけ(単体テストは実装手順書の作業単位ごとのテスト観点で見る)
TITLE = "# テスト仕様書(結合・総合)"
UNIT_TEST_NOTE = "※単体テストのテスト観点は実装手順書で個別に確認"
_NONE_ROW = "(なし)"
_NO_TEST = "**テストが無い**"


def _text(value: str) -> str:
    return value or "—"


def expected_cell(case: SpecCase) -> str:
    """期待結果(設計で決まっていないことがあれば、後ろに印を付ける)。"""
    if not case.design_gap:
        return _text(case.expected)
    return f"{_text(case.expected)}(**設計に無い**: {case.design_gap})"


def _links(case: SpecCase) -> str:
    parts = [ids_cell(case.functions), ids_cell(case.units)]
    return "—" if parts == ["—", "—"] else " / ".join(parts)


def _policy(model: TestSpecModel) -> list[str]:
    p = model.policy
    return md_table(
        ["項目", "内容"],
        [
            ["対象", _text(p.target)],
            ["範囲外", _text(p.out_of_scope)],
            ["観点", "・".join(VIEWPOINTS)],
            ["環境", _text(p.environment)],
            ["完了条件", _text(p.done_criteria)],
        ],
    )


def _integration(cases: Sequence[SpecCase]) -> list[str]:
    if not cases:
        return [_NONE_ROW]
    return md_table(
        ["テストID", "対象", "観点", "前提", "手順・入力", "期待結果", "要件", "処理・単位"],
        [
            [
                c.id,
                c.target,
                c.viewpoint,
                _text(c.precondition),
                _text(c.steps),
                expected_cell(c),
                ids_cell(c.req_ids),
                _links(c),
            ]
            for c in cases
        ],
    )


def _system(cases: Sequence[SpecCase]) -> list[str]:
    if not cases:
        return [_NONE_ROW]
    return md_table(
        ["テストID", "対象", "観点", "手順", "期待結果", "要件"],
        [
            [c.id, c.target, c.viewpoint, _text(c.steps), expected_cell(c), ids_cell(c.req_ids)]
            for c in cases
        ],
    )


def trace_rows(model: TestSpecModel) -> list[tuple[str, str, list[str]]]:
    """要件との対応の行(要件ID・優先度・テストID)。Must の機能要件を先に、続けてテストが指す
    ほかの要件を要件定義書の順に(写しに無い要件はテストに出た順に)並べる。"""
    referenced = {r for c in model.cases for r in c.req_ids}
    order = [*model.must_fr_ids]
    order += [r for r in model.known_req_ids if r in referenced and r not in order]
    order += [r for c in model.cases for r in c.req_ids if r not in order]
    order = list(dict.fromkeys(order))
    return [(r, model.req_priorities.get(r, "―"), model.tests_for(r)) for r in order]


def _trace(model: TestSpecModel) -> list[str]:
    rows = trace_rows(model)
    if not rows:
        return [_NONE_ROW]
    return md_table(
        ["要件", "優先度", "テスト"],
        [[r, priority, "・".join(tests) or _NO_TEST] for r, priority, tests in rows],
    )


def render_test_spec(model: TestSpecModel) -> str:
    """テスト仕様書の md。"""
    lines = [TITLE, "", UNIT_TEST_NOTE, "", "## 1 テストの方針", "", *_policy(model)]
    lines += ["", "## 2 結合テスト(API)", "", *_integration(model.integration)]
    lines += ["", "## 3 総合テスト(画面の流れ・非機能)", "", *_system(model.system)]
    lines += ["", "## 4 要件との対応", "", *_trace(model)]
    gaps = sum(1 for c in model.cases if c.design_gap)
    if gaps:
        lines += [
            "",
            f"「設計に無い」と印を付けたテストが {gaps} 件あります。テストを書く前に設計を直して"
            "ください。",
        ]
    return "\n".join(lines) + "\n"
