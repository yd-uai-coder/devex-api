"""API キーの暗号化(Fernet と鍵の回し方)のテスト。

SUT: app/core/crypto.py の build_cipher・encryption_available・encrypt_secret・decrypt_secret
ドライバ: pytest のテスト関数
スタブ不要 ── 暗号化は外部を呼ばない。鍵は設定(settings.ENCRYPTION_KEY)を monkeypatch で替える
"""

import pytest
from cryptography.fernet import Fernet

from app.core import crypto
from app.core.config import settings
from app.services.errors import DecryptionFailedError, EncryptionUnavailableError


def test_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", Fernet.generate_key().decode())

    token = crypto.encrypt_secret("sk-secret-1234")

    assert token != "sk-secret-1234"
    assert crypto.decrypt_secret(token) == "sk-secret-1234"
    assert crypto.encryption_available() is True


def test_rotated_key_still_decrypts_old_token(monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", old)
    token = crypto.encrypt_secret("old-secret")

    # 新しい鍵を先頭に足し、古い鍵を後ろに残す
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", f"{new}, {old}")

    assert crypto.decrypt_secret(token) == "old-secret"


def test_dropped_old_key_cannot_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", Fernet.generate_key().decode())
    token = crypto.encrypt_secret("old-secret")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", Fernet.generate_key().decode())

    with pytest.raises(DecryptionFailedError):
        crypto.decrypt_secret(token)


@pytest.mark.parametrize("raw", [None, "", " , ", "not-a-fernet-key"])
def test_missing_or_malformed_key_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, raw: str | None
) -> None:
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", raw)

    assert crypto.encryption_available() is False
    with pytest.raises(EncryptionUnavailableError) as exc_info:
        crypto.encrypt_secret("x")
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "ENCRYPTION_UNAVAILABLE"
