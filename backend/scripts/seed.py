"""開発用のシードデータ ── ブラウザで `/docs` からログインを試すためのテストユーザーを 1 人作る。

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
    """テストユーザーを1人登録し、固定プロンプトテンプレートを投入する(いずれも既存ならスキップ)。"""
    async with AsyncSessionLocal() as session:
        await _seed_user(session)
        await seed_prompt_templates(session)


async def _seed_user(session: AsyncSession) -> None:
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

