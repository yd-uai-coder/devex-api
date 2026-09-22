"""リクエストボディのサイズ上限ミドルウェア。

nginx の `client_max_body_size` はアプリを直接公開した開発環境や、nginx を通らない経路
(docker 内部ネットワーク)では効かない。アプリ側にも上限を置き、巨大なリクエストによる
CPU・メモリの浪費を入口で止める。
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _BodyTooLargeError(Exception):
    """ストリーミングで受信済みのボディが上限を超えたことを内部で伝える例外。"""


class BodySizeLimitMiddleware:
    """`max_bytes` を超えるリクエストボディを 413 で拒否する ASGI ミドルウェア。

    Content-Length があれば読み込む前に拒否する。チャンク転送のように Content-Length が
    無い(または偽られた)場合は、受信したバイト数を数えて超過した時点で拒否する。
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        # max_bytes: 許容するリクエストボディの最大バイト数
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 宣言された Content-Length が既に上限超過なら、ボディを 1 バイトも読まずに拒否する
        content_length = dict(scope["headers"]).get(b"content-length")
        if (
            content_length is not None
            and content_length.isdigit()
            and int(content_length) > self.max_bytes
        ):
            await self._reject(send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLargeError:
            # 応答を書き始めた後では 413 に切り替えられない(そのまま接続エラーにする)
            if response_started:
                raise
            await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"detail":"request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
