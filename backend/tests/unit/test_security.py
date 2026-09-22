from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_password_produces_argon2_hash() -> None:
    """hash_passwordがArgon2形式のハッシュ文字列を返すことを確認する。"""
    hashed = hash_password("correct horse battery staple")

    assert hashed.startswith("$argon2")
    assert hashed != "correct horse battery staple"


def test_verify_password_accepts_correct_password() -> None:
    """正しいパスワードを渡した場合にverify_passwordがTrueを返すことを確認する。"""
    hashed = hash_password("my-secret")

    assert verify_password("my-secret", hashed) is True


def test_verify_password_rejects_incorrect_password() -> None:
    """誤ったパスワードを渡した場合にverify_passwordがFalseを返すことを確認する。"""
    hashed = hash_password("my-secret")

    assert verify_password("wrong-password", hashed) is False


def test_create_access_token_has_expected_claims() -> None:
    """アクセストークンにsub/type/jtiが期待通り含まれることを確認する。"""
    token = create_access_token("user-123")

    claims = decode_token(token)

    assert claims["sub"] == "user-123"
    assert claims["type"] == TokenType.ACCESS.value
    assert "jti" in claims


def test_create_refresh_token_has_expected_claims() -> None:
    """リフレッシュトークンにsub/typeが期待通り含まれることを確認する。"""
    token = create_refresh_token("user-123")

    claims = decode_token(token)

    assert claims["sub"] == "user-123"
    assert claims["type"] == TokenType.REFRESH.value


def test_access_and_refresh_tokens_get_distinct_jti() -> None:
    """アクセストークンとリフレッシュトークンでjtiが別々に発行されることを確認する。"""
    access_token = create_access_token("user-123")
    refresh_token = create_refresh_token("user-123")

    assert decode_token(access_token)["jti"] != decode_token(refresh_token)["jti"]


def test_decode_token_raises_on_tampered_signature() -> None:
    """署名部分が改ざんされたトークンをdecode_tokenが拒否することを確認する。"""
    token = create_access_token("user-123")

    with pytest.raises(jwt.PyJWTError):
        decode_token(token + "tampered")


def test_decode_token_raises_on_expired_token() -> None:
    """有効期限切れのトークンをdecode_tokenが拒否することを確認する。"""
    now = datetime.now(UTC)
    expired_payload = {
        "sub": "user-123",
        "type": TokenType.ACCESS.value,
        "iat": now - timedelta(minutes=10),
        "exp": now - timedelta(minutes=5),
    }
    expired_token = jwt.encode(
        expired_payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(expired_token)
