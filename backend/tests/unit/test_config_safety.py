"""`Settings._reject_unsafe_production_settings`(本番設定の安全バリデータ)。

テスト対象 / ドライバ / スタブ:
- 対象: `app.core.config.Settings`
- ドライバ: このテスト関数
- スタブ不要 ── Settings の構築だけで完結する(DB・Redis を使わない)
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

_BASE = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
    "REDIS_URL": "redis://localhost",
    "JWT_SECRET_KEY": "x" * 32,
}


def test_safe_production_settings_do_not_raise() -> None:
    Settings(ENVIRONMENT="production", DEBUG=False, **_BASE)  # type: ignore[call-arg]


def test_debug_true_in_production_is_rejected() -> None:
    with pytest.raises(ValueError, match="DEBUG=true"):
        Settings(ENVIRONMENT="production", DEBUG=True, **_BASE)  # type: ignore[call-arg]


def test_weak_jwt_secret_in_production_is_rejected() -> None:
    weak = {**_BASE, "JWT_SECRET_KEY": "change-me-please"}
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(ENVIRONMENT="production", DEBUG=False, **weak)  # type: ignore[call-arg]


def test_unsafe_settings_are_allowed_outside_production() -> None:
    weak = {**_BASE, "JWT_SECRET_KEY": "short"}
    Settings(ENVIRONMENT="development", DEBUG=True, **weak)  # type: ignore[call-arg]
