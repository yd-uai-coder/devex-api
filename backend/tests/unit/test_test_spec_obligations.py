"""テスト仕様書の必要なテストの一覧のテスト。

SUT は`app/documents/test_spec/obligations.py`の`required_tests`・`error_statuses`・
`obligation_rows`、ドライバはこのテスト。スタブ不要 ── 対象が純粋関数で、要件定義書・外部設計書の
model だけから決まるため。
"""

from tests.fixtures.spec_documents import (
    CREATE_API,
    DELETE_API,
    LIST_API,
    spec_external,
    spec_requirements,
)

from app.documents.test_spec import error_statuses, obligation_rows, required_tests


def _viewpoints(target: str) -> list[str]:
    obligations = required_tests(spec_requirements(), spec_external())
    return [o.viewpoint for o in obligations if o.target == target]


def test_error_statuses_takes_4xx_and_5xx_in_order():
    assert error_statuses("400 入力不正 / 409 重複・500、400") == ["400", "409", "500"]
    assert error_statuses("201 作成・ID 12345") == []


def test_each_error_status_becomes_an_error_test():
    assert _viewpoints(CREATE_API) == ["正常", "異常", "異常", "境界"]


def test_api_without_statuses_still_needs_one_error_test():
    assert _viewpoints(DELETE_API).count("異常") == 1


def test_path_parameter_needs_security_test_and_no_body_skips_boundary():
    assert _viewpoints(DELETE_API) == ["正常", "異常", "セキュリティ"]
    assert "境界" not in _viewpoints(LIST_API)


def test_system_tests_cover_must_screens_nfr_and_unlinked_must():
    obligations = required_tests(spec_requirements(), spec_external())
    system = [o for o in obligations if o.level == "system"]
    assert [(o.target, o.viewpoint) for o in system] == [
        ("SCR-001", "正常"),
        ("性能", "非機能"),
        ("FR-002", "正常"),
    ]
    assert system[1].req_ids == ("NFR-001",)


def test_without_requirements_only_api_and_screen_tests():
    obligations = required_tests(None, spec_external())
    assert {o.level for o in obligations if o.target.startswith("SCR")} == {"system"}
    assert all("FR-002" not in o.req_ids for o in obligations)


def test_obligation_rows_are_pipe_separated():
    rows = obligation_rows(required_tests(spec_requirements(), spec_external()))
    assert rows[0] == f"結合|{LIST_API}|正常|正しい入力で成功する|FR-001|SCR-001"
