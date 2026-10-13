"""開発用のシードデータ ── `/docs` からログインを試すテストユーザー(一般・superuser)を作る。

使い方:
    uv run python -m scripts.seed
    # docker:
    docker compose run --rm backend uv run python -m scripts.seed

冪等 ── 既に居れば何もしない。**dev 専用**(本番 DB では実行しない)。
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.prompt_template import PromptTemplate
from app.repositories.prompt_template import PromptTemplateRepository
from app.services.errors import UserAlreadyExistsError
from app.services.user import UserService

# ログインフォームに入れる固定ユーザー(full_name = 表示名。ログインは email + password)
SEED_USER = {
    "full_name": "sample-user",
    "email": "example-user@example.com",
    "password": "Sample-user-0123",
}

# superuser(レビュー機能のオン・オフを切り替えられるアカウント)
SEED_SUPERUSER = {
    "full_name": "sample-super-user",
    "email": "example-super-user@example.com",
    "password": "Sample-super-user-0123",
}


SEED_PROMPT_TEMPLATES = [
    {
        "name": "Webアプリケーション標準",
        "target_type": "SaaS・BtoC/BtoB Webサービス",
        "system_prompt": (
            "あなたは優秀なシステムアーキテクトです。一般的なWebアプリケーション"
            "(ブラウザから利用するSaaS/BtoC・BtoBサービス)を前提に、画面遷移・認証・"
            "権限設計の観点も含めてヒアリングを進めてください。"
        ),
        "default_environment": {
            "languages": ["TypeScript", "Python"],
            "frameworks": ["Next.js", "FastAPI"],
            "databases": ["PostgreSQL"],
            "deploy_targets": [],
        },
    },
    {
        "name": "API向け",
        "target_type": "バックエンドAPI・サービス間連携",
        "system_prompt": (
            "あなたは優秀なバックエンドアーキテクトです。画面を持たないAPI・"
            "サービス間連携を前提に、エンドポイント設計・認証方式・レート制限・"
            "外部システムとの連携方式の観点も含めてヒアリングを進めてください。"
        ),
        "default_environment": {
            "languages": ["Python"],
            "frameworks": ["FastAPI"],
            "databases": ["PostgreSQL"],
            "deploy_targets": [],
        },
    },
]


async def seed() -> None:
    """テストユーザー(一般・superuser)を登録し、固定プロンプトテンプレートを投入する(いずれも既存ならスキップ)。"""
    async with AsyncSessionLocal() as session:
        await seed_users(session)
        await seed_prompt_templates(session)


async def seed_users(session: AsyncSession) -> None:
    """一般ユーザーと superuser を作る(email 一致で冪等判定。既存ユーザーの権限は変えない)。"""
    service = UserService(session)
    for data, is_superuser in ((SEED_USER, False), (SEED_SUPERUSER, True)):
        try:
            user = await service.create_user(
                email=data["email"],
                password=data["password"],
                full_name=data["full_name"],
            )
        except UserAlreadyExistsError:
            print(f"user already exists: {data['email']} (skip)")
            continue
        if is_superuser:
            # 登録の経路(create_user)からは superuser を作れないため、作成後に昇格させる
            user.is_superuser = True
            await session.commit()
        print(f"created user: {user.email} ({user.id}, superuser={user.is_superuser})")


async def seed_prompt_templates(session: AsyncSession) -> None:
    """SEED_PROMPT_TEMPLATESの各テンプレートを、同名のものが無ければ作成する(name一致で冪等判定)。"""
    repo = PromptTemplateRepository(session)
    for data in SEED_PROMPT_TEMPLATES:
        existing = await repo.find_one(name=data["name"])
        if existing is not None:
            print(f"prompt template already exists: {data['name']} (skip)")
            continue
        template = PromptTemplate(**data)
        session.add(template)
        await session.flush()
        print(f"created prompt template: {template.name} ({template.id})")
    await session.commit()


if __name__ == "__main__":
    asyncio.run(seed())

