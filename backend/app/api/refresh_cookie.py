from fastapi import Response

from app.api.deps import REFRESH_TOKEN_COOKIE_NAME
from app.core.config import settings

# Cookie の寿命は JWT・Redis の TTL と同じ設定から導く(食い違うと、サーバーでは有効なのに
# Cookie が消えていて使えない期間ができる)
REFRESH_TOKEN_MAX_AGE_SECONDS = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
REFRESH_TOKEN_COOKIE_PATH = "/api/v1/auth"


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """リフレッシュトークンをhttpOnly Secure Cookieに設定する。pathを/api/v1/authに限定し、
    認証系以外のAPIへの送信を防ぐ(不要な露出面を減らす)。samesite=laxは同一サイト内の遷移・
    リロードでは送信されつつ、クロスサイトの単純なPOST等では送信されないバランス。secureは
    本番(ENVIRONMENT=production)でのみ強制する(開発環境はHTTPのため常時強制すると送信されなくなる)。
    ログイン(routes/auth.py)とパスワードの変更(routes/users.py)の両方が付ける。"""
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=REFRESH_TOKEN_MAX_AGE_SECONDS,
        path=REFRESH_TOKEN_COOKIE_PATH,
    )


def delete_refresh_cookie(response: Response) -> None:
    """リフレッシュトークンのCookieを消す(ログアウト)。付けたときと同じpathを指定する。"""
    response.delete_cookie(REFRESH_TOKEN_COOKIE_NAME, path=REFRESH_TOKEN_COOKIE_PATH)
