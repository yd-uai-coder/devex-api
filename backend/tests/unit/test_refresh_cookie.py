"""refresh の Cookie の寿命が、トークンの寿命と揃っていることのテスト。

SUT: refresh_cookie.set_refresh_cookie
ドライバ: テスト関数
スタブ不要 ── 応答の Cookie のヘッダーを組み立てるだけで、外部依存を呼ばないため。
"""

from fastapi import Response

from app.api import refresh_cookie
from app.core.config import settings


def test_refresh_cookie_lives_as_long_as_the_token() -> None:
    response = Response()

    refresh_cookie.set_refresh_cookie(response, "token")

    assert settings.REFRESH_TOKEN_EXPIRE_DAYS == 14
    cookie = response.headers["set-cookie"]
    assert f"Max-Age={14 * 24 * 3600}" in cookie
