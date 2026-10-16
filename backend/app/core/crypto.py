"""ユーザーが登録する API キーの暗号化(Fernet)。

鍵は環境変数 `ENCRYPTION_KEY`(カンマ区切りで複数)。`MultiFernet` は先頭の鍵で暗号化し、
すべての鍵で復号を試す。鍵を回すときは新しい鍵を先頭に足し、古い鍵を後ろに残せば、登録し直さずに
読める。鍵が無いときは、キーの登録だけを断る(既定のキーでの利用は続く)。
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings
from app.services.errors import DecryptionFailedError, EncryptionUnavailableError


def build_cipher(raw_keys: str | None) -> MultiFernet:
    """カンマ区切りの鍵の文字列から `MultiFernet` を組み立てる。
    空・不正なら EncryptionUnavailableError。"""
    keys = [key.strip() for key in (raw_keys or "").split(",") if key.strip()]
    if not keys:
        raise EncryptionUnavailableError(
            "APIキーを保存する暗号の鍵がサーバーに設定されていません。"
        )
    try:
        return MultiFernet([Fernet(key) for key in keys])
    except ValueError as exc:
        raise EncryptionUnavailableError("サーバーの暗号の鍵の形が正しくありません。") from exc


def encryption_available() -> bool:
    """暗号の鍵が使える形で設定されているか(画面の出し分けに使う)。"""
    try:
        build_cipher(settings.ENCRYPTION_KEY)
    except EncryptionUnavailableError:
        return False
    return True


def encrypt_secret(plain: str) -> str:
    """平文を先頭の鍵で暗号化し、トークン(文字列)で返す。"""
    return build_cipher(settings.ENCRYPTION_KEY).encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    """暗号文を復号する。どの鍵でも復号できなければ DecryptionFailedError。"""
    try:
        return build_cipher(settings.ENCRYPTION_KEY).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionFailedError("暗号文を今の鍵で復号できません。") from exc
