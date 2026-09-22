"""開発用のシードデータ ── ブラウザで `/docs` からログインを試すためのテストユーザーを 1 人作る。

使い方:
    uv run python -m scripts.seed
    # docker:
    docker compose run --rm backend uv run python -m scripts.seed

冪等 ── 既に居れば何もしない。**dev 専用**(本番 DB では実行しない)。
"""

from __future__ import annotations

import asyncio

from app.core.database import AsyncSessionLocal
from app.services.errors import UserAlreadyExistsError
from app.services.user import UserService

# ログインフォームに入れる固定ユーザー(full_name = 表示名。ログインは email + password)
SEED_USER = {
    "full_name": "sample-user",
    "email": "example-user@example.com",
    "password": "sample-user-0123",
}


async def seed() -> None:
    """テストユーザーを 1 人登録する(既存ならスキップ)。"""
    async with AsyncSessionLocal() as session:
        service = UserService(session)
        try:
            user = await service.create_user(
                email=SEED_USER["email"],
                password=SEED_USER["password"],
                full_name=SEED_USER["full_name"],
            )
            print(f"created user: {user.email} ({user.id})")
        except UserAlreadyExistsError:
            print(f"user already exists: {SEED_USER['email']} (skip)")


if __name__ == "__main__":
    asyncio.run(seed())
