"""パスワードの規則のテスト。

SUT: app.core.password_policy.password_violations と、それを使う PasswordUpdate の検証
ドライバ: pytest のテスト関数
スタブ不要 ── 規則は純粋な関数で、外部の依存を呼ばないため
"""

import pytest
from pydantic import ValidationError

from app.core.password_policy import PASSWORD_MAX_LENGTH, password_violations
from app.schemas.user import PasswordUpdate


def test_valid_password_has_no_violations() -> None:
    assert password_violations("Abcdef1!") == []


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        ("Abcde1!", "8〜128文字"),
        ("A" * PASSWORD_MAX_LENGTH + "a1!", "8〜128文字"),
        ("abcdefg1!", "大文字"),
        ("ABCDEFG1!", "小文字"),
        ("Abcdefgh!", "数字"),
        ("Abcdefgh1", "記号"),
        # 全角の記号は数えない(画面の SYMBOL_PATTERN と同じく ASCII の記号だけ)
        ("Abcdefg1！", "記号"),
    ],
)
def test_each_rule_reports_its_violation(password: str, expected: str) -> None:
    violations = password_violations(password)

    assert len(violations) == 1
    assert expected in violations[0]


def test_password_update_rejects_new_password_against_rules() -> None:
    with pytest.raises(ValidationError, match="大文字"):
        PasswordUpdate(current_password="anything", new_password="abcdefg1!")


def test_password_update_does_not_check_current_password_rules() -> None:
    # 今のパスワードは規則より前に作ったものでもよい(登録の API は規則を課さないため)
    body = PasswordUpdate(current_password="s3cret", new_password="Abcdef1!")

    assert body.current_password == "s3cret"
