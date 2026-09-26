"""CORSMiddlewareのexpose_headers設定。

テスト対象 / ドライバ / スタブ:
- 対象: `app.main.app`に登録されたCORSMiddlewareの設定
- ドライバ: `app.user_middleware`を直接検査(DB/Redis不要)
- スタブ不要 ── ミドルウェア登録内容を読むだけで、対象が純粋なため
"""

from starlette.middleware.cors import CORSMiddleware

from app.main import app


def test_cors_exposes_content_disposition_header() -> None:
    """Content-DispositionはブラウザのCORSセーフリスト対象外のレスポンスヘッダーのため、
    expose_headersで明示しない限りクロスオリジンのfetch()からJSで読めない
    (devex-uiのdocumentsApi.tsはこれが読めない場合、フォールバックとしてドキュメントの
    UUIDをファイル名にしてしまう。実際に発生した不具合の回帰テスト)。"""
    cors_middleware = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    expose_headers = cors_middleware.kwargs.get("expose_headers", [])

    assert isinstance(expose_headers, list)
    assert "Content-Disposition" in expose_headers
