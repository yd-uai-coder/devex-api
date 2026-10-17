"""テスト仕様書の決定的な検証(純粋関数)。指摘は警告だけで、生成も保存も止めない。

レビューには渡さず、機械で調べられるものだけを見る(Phase 51-2 の決定): Must の機能要件にテストが
無い・存在しない要件 / API / 画面を指す・異常系のテストが1件も無い API・要件を言い直しただけの
期待結果(「要件どおり」「提供されること」)。期待結果と設計の食い違い・境界値の取り方・認可の
テストの抜けは見ない。要件・API・画面は生成した時点の写し(`known_*`)と
照らし、写しが空ならその照合はしない。
"""

import re
from collections.abc import Mapping

from app.documents.issues import DocumentIssue, IssueLevel
from app.documents.test_spec.assemble import api_key
from app.documents.test_spec.model import ERROR_VIEWPOINT, TestSpecModel

# 要件を言い直しただけの期待結果(何が見える・残るかを書いていない)
_SHALLOW_EXPECTED = re.compile(r"要件(どおり|通り)|提供される(こと)?|正しく(動作|機能)する")


def row_counts(model: TestSpecModel) -> dict[str, int]:
    """結合・総合のテストの件数。"""
    return {"integration": len(model.integration), "system": len(model.system)}


def design_gap_count(model: TestSpecModel) -> int:
    """「設計に無い」と印を付けたテストの件数(テストを書く前に設計を直すもの)。"""
    return sum(1 for case in model.cases if case.design_gap)


def is_outdated(model: TestSpecModel, current_inputs: Mapping[str, int | None]) -> bool:
    """生成した時点の入力の版が今と違うか(入力の版を持たない版は古いとみなさない)。"""
    return bool(model.inputs) and dict(model.inputs) != dict(current_inputs)


def _issue(level: IssueLevel, code: str, message: str, targets: list[str]) -> DocumentIssue:
    return DocumentIssue(level, code, message + ": " + "・".join(targets), tuple(targets))


def validate_test_spec(model: TestSpecModel) -> list[DocumentIssue]:
    issues: list[DocumentIssue] = []
    untested = [r for r in model.must_fr_ids if not model.tests_for(r)]
    if untested:
        issues.append(
            _issue("major", "MUST_REQUIREMENT_UNTESTED", "テストの無い Must の機能要件", untested)
        )
    if model.known_req_ids:
        known = set(model.known_req_ids)
        referenced = (r for c in model.cases for r in c.req_ids)
        unknown = list(dict.fromkeys(r for r in referenced if r not in known))
        if unknown:
            issues.append(
                _issue("major", "UNKNOWN_REQUIREMENT", "要件定義書に無い要件ID", unknown)
            )
    known_apis = {api_key(a): a for a in model.known_apis}
    if known_apis:
        targets = (c.target for c in model.integration)
        unknown_apis = list(dict.fromkeys(t for t in targets if api_key(t) not in known_apis))
        if unknown_apis:
            issues.append(
                _issue("major", "UNKNOWN_API", "外部設計書に無い API", unknown_apis)
            )
    if model.known_screens:
        known_screens = set(model.known_screens)
        unknown_screens = list(
            dict.fromkeys(s for c in model.cases for s in c.screens if s not in known_screens)
        )
        if unknown_screens:
            issues.append(
                _issue("minor", "UNKNOWN_SCREEN", "外部設計書に無い画面ID", unknown_screens)
            )
    tested = {api_key(c.target) for c in model.integration if c.viewpoint == ERROR_VIEWPOINT}
    no_error = [label for key, label in known_apis.items() if key not in tested]
    if no_error:
        issues.append(
            _issue("minor", "API_WITHOUT_ERROR_TEST", "異常系のテストが無い API", no_error)
        )
    shallow = [
        c.id for c in model.system if c.req_ids and _SHALLOW_EXPECTED.search(c.expected)
    ]
    if shallow:
        issues.append(
            _issue(
                "minor",
                "SHALLOW_REQUIREMENT_TEST",
                "期待結果が要件の言い直しのテスト(何が見える・残るかを書く)",
                shallow,
            )
        )
    return issues
