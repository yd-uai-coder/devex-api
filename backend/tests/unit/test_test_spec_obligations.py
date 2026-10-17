"""テスト仕様書の必要なテストの一覧のテスト。

SUT は`app/documents/test_spec/obligations.py`の`required_tests`・`error_statuses`・`body_ids`・
`body_tokens`・`obligation_rows`、ドライバはこのテスト。スタブ不要 ── 対象が純粋関数で、
要件定義書・外部設計書の model だけから決まるため。
"""

from tests.fixtures.spec_documents import (
    CREATE_API,
    DELETE_API,
    LIST_API,
    spec_external,
    spec_requirements,
)

from app.documents.requirements.model import RequirementRow
from app.documents.test_spec import error_statuses, obligation_rows, required_tests
from app.documents.test_spec.obligations import body_ids, body_tokens


def _viewpoints(target: str) -> list[str]:
    obligations = required_tests(spec_requirements(), spec_external())
    return [o.viewpoint for o in obligations if o.target == target]


def test_error_statuses_takes_4xx_and_5xx_in_order():
    assert error_statuses("400 入力不正 / 409 重複・500、400") == ["400", "409", "500"]
    assert error_statuses("201 作成・ID 12345") == []


def test_each_error_status_becomes_an_error_test():
    assert _viewpoints(CREATE_API) == ["正常", "異常", "異常", "境界"]


def _reasons(target: str, external=None) -> list[str]:  # noqa: ANN001
    obligations = required_tests(spec_requirements(), external or spec_external())
    return [o.reason for o in obligations if o.target == target]


def test_api_without_statuses_still_needs_one_error_test():
    assert "エラー欄に HTTP ステータスが無い。失敗の扱いを確かめる" in _reasons(DELETE_API)


def test_path_parameter_needs_security_test_and_no_body_skips_boundary():
    # 異常の2件目は、パスに {id} を持ち、エラー欄に 401 が無いことの未認証(下のテスト)
    assert _viewpoints(DELETE_API) == ["正常", "異常", "異常", "セキュリティ"]
    assert "境界" not in _viewpoints(LIST_API)


def test_path_parameter_api_without_401_needs_unauthenticated_test_when_design_uses_auth():
    """設計のどこかの API が 401 を返すなら、エラー欄に 401 の無い {id} の API にも未認証を置く。
    401 を返す API が無い(ログインの無い)設計には置かない。"""
    assert "認証なしで呼ぶ(401)" in _reasons(DELETE_API)
    assert "認証なしで呼ぶ(401)" not in _reasons(CREATE_API)  # {id} が無い
    external = spec_external()
    external.apis[0].errors = "500 失敗"
    assert "認証なしで呼ぶ(401)" not in _reasons(DELETE_API, external)
    external.apis[2].errors = "401 未ログイン"
    external.apis[0].errors = "401 未ログイン"
    assert _reasons(DELETE_API, external).count("エラー欄の 401 を起こす") == 1
    assert "認証なしで呼ぶ(401)" not in _reasons(DELETE_API, external)


def test_system_tests_cover_must_screens_nfr_and_every_must_requirement():
    """Must の画面の流れ・Must の画面ごとの操作・非機能・Must の要件ごとの中身(API に結ばれた
    FR-001 も)。Should の要件・画面は入れない。"""
    obligations = required_tests(spec_requirements(), spec_external())
    system = [o for o in obligations if o.level == "system"]
    assert [(o.target, o.viewpoint) for o in system] == [
        ("SCR-001", "正常"),
        ("SCR-001", "異常"),
        ("性能", "非機能"),
        ("FR-001", "正常"),
        ("FR-002", "正常"),
    ]
    assert system[1].reason == "画面の操作: 入力の検証・空の送信・エラーの表示"
    assert system[2].req_ids == ("NFR-001",)
    assert system[3].reason == "要件の中身: 予約できる"
    assert system[3].screens == ("SCR-001",)
    assert system[4].reason == "API・画面に無い Must の要件: 予約を通知する"


def test_failure_requirement_checks_state_after_failure():
    """失敗・中断を書く要件は、異常の観点で、失敗の後の状態を確かめる。"""
    requirements = spec_requirements()
    requirements.rows[1].text = "通知に失敗したら、もう一度送れる"

    [row] = [o for o in required_tests(requirements, spec_external()) if o.target == "FR-002"]

    assert row.viewpoint == "異常"
    assert row.reason.endswith("。失敗の後の状態(保存されない・やり直せる)を確かめる")


def test_body_ids_need_security_tests():
    """本文で ID を受ける API(`session_id`)にも、ほかの利用者のリソースのテストを置く。"""
    names = body_ids("session_id(必須)・message・projectId・session_id")
    assert names == ["session_id", "projectId"]
    assert body_ids("メッセージ本文・id_token なし") == []
    external = spec_external()
    external.apis[1].body = "session_id・メッセージ"

    reasons = [o.reason for o in required_tests(None, external) if o.target == CREATE_API]

    assert "本文の session_id でほかの利用者のリソースを指す" in reasons


def test_body_tokens_need_security_tests():
    """本文のトークンで本人を決める API(`token`)にも、ほかの利用者・無効なトークンのテストを
    置く。"""
    assert body_tokens("token・menu_name・refresh_token・token") == ["token", "refresh_token"]
    assert body_tokens("tokens・tokenize・トークン") == []
    external = spec_external()
    external.apis[1].body = "token・日付"

    reasons = [o.reason for o in required_tests(None, external) if o.target == CREATE_API]

    assert "本文の token をほかの利用者のもの・無効なものにする" in reasons


def test_limit_nfr_and_external_limit_constraint_need_display_tests():
    """上限・回数を書く非機能要件は、超えたときの応答と画面の表示も確かめる。外部サービスの上限を
    書く制約(CON)は、総合の異常系を1件置く(上限を書かない制約・非機能要件には足さない)。"""
    requirements = spec_requirements()
    requirements.rows += [
        RequirementRow(id="NFR-002", kind="NFR", category="不正利用", text="ログインは1分5回まで",
                       source="user"),
        RequirementRow(id="CON-001", kind="CON", category="外部", text="Gemini の無料枠で動かす",
                       source="user"),
        RequirementRow(id="CON-002", kind="CON", category="技術", text="Python で書く",
                       source="user"),
    ]

    obligations = required_tests(requirements, spec_external())
    by_req = {o.req_ids: o for o in obligations if o.level == "system"}

    assert by_req[("NFR-001",)].reason == "3秒以内に応答する"
    assert by_req[("NFR-002",)].reason == (
        "ログインは1分5回まで。上限を超えたときの応答と、画面での伝え方を確かめる"
    )
    con = by_req[("CON-001",)]
    assert (con.target, con.viewpoint) == ("CON-001", "異常")
    assert con.reason.endswith("。外部サービスの上限超過・失敗のときの応答と画面の表示を確かめる")
    assert ("CON-002",) not in by_req


def test_without_requirements_only_api_and_screen_tests():
    obligations = required_tests(None, spec_external())
    assert {o.level for o in obligations if o.target.startswith("SCR")} == {"system"}
    assert all("FR-002" not in o.req_ids for o in obligations)


def test_obligation_rows_are_pipe_separated():
    rows = obligation_rows(required_tests(spec_requirements(), spec_external()))
    assert rows[0] == f"結合|{LIST_API}|正常|正しい入力で成功する|FR-001|SCR-001"
