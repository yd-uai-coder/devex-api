"""`app.core.logging.configure_logging`。

テスト対象(SUT) / ドライバ / スタブ:
- 対象: `configure_logging`
- ドライバ: このテスト関数(直接呼び出し)
- スタブ不要 ── structlogのグローバル設定を変更するだけで、DB・外部APIを呼ばない
  (設定内容の検証は`structlog.get_config()`を読むことで行う)。
"""

import structlog

from app.core.logging import configure_logging

# structlogのグローバル設定を書き換えた後の後始末はtests/conftest.pyの
# _reset_structlog_state(autouse)が行う。


def test_configure_logging_sets_json_renderer_and_does_not_raise() -> None:
    configure_logging()

    config = structlog.get_config()
    processor_names = [type(p).__name__ for p in config["processors"]]
    assert "JSONRenderer" in processor_names


def test_configure_logging_is_idempotent() -> None:
    """アプリ起動時に複数回呼ばれても(テストの都度呼ばれる等)例外を送出しない。"""
    configure_logging()
    configure_logging()
