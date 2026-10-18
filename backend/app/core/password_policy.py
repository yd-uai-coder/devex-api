"""パスワードの規則(画面の登録・変更のフォームと同じ。devex-ui の features/auth/schemas.ts)。

純粋な関数として置き、変更の API のスキーマ(app/schemas/user.py の PasswordUpdate)が使う。
登録の API には課さない(既存のアカウントと、規則より前のテストのパスワードを断らないため)。
"""

import re

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

# (満たすべき文字の種類, 満たさないときの文言)
_CHARACTER_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[A-Z]"), "パスワードは大文字を含めてください"),
    (re.compile(r"[a-z]"), "パスワードは小文字を含めてください"),
    (re.compile(r"[0-9]"), "パスワードは数字を含めてください"),
    # 記号は ASCII の記号(画面の validation-rules.ts の SYMBOL_PATTERN と同じ集合)
    (re.compile(r"[!-/:-@\[-`{-~]"), "パスワードは記号を含めてください"),
)


def password_violations(password: str) -> list[str]:
    """規則に合わない点を文言の一覧で返す(合っていれば空)。"""
    violations: list[str] = []
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        violations.append(
            f"パスワードは{PASSWORD_MIN_LENGTH}〜{PASSWORD_MAX_LENGTH}文字で入力してください"
        )
    violations.extend(
        message for pattern, message in _CHARACTER_RULES if not pattern.search(password)
    )
    return violations
