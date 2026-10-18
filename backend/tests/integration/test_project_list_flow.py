"""プロジェクトの一覧の検索・絞込みを実際の PostgreSQL で確かめる(ILIKE とエスケープ)。

SUT: GET /api/v1/projects(ルート → ProjectService.list_page → ProjectRepository.search_for_user)
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ不要 ── ユニットテストの SQLite は ILIKE を lower() の LIKE に置き換えるため、
Postgres の ILIKE とエスケープの振る舞いそのものを実際の DB で確かめる
(利用者・プロジェクトはセッションで直接作り、アクセストークンも直接発行する ──
API で登録すると、スイート全体で登録・ログインの IP ごとの上限に当たるため)。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.project import Project
from app.models.user import User

pytestmark = pytest.mark.integration


async def _user_with_projects(email: str, rows: list[tuple[str, str, str]]) -> dict[str, str]:
    """(タイトル, モード, 状態)の行を、並びの新しい順に作る。"""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    async with AsyncSessionLocal() as session:
        user = User(email=email, hashed_password="x")
        session.add(user)
        await session.flush()
        for i, (title, mode, status) in enumerate(rows):
            session.add(
                Project(
                    user_id=user.id,
                    title=title,
                    mode=mode,
                    status=status,
                    updated_at=base - timedelta(minutes=i),
                )
            )
        await session.commit()
        user_id: uuid.UUID = user.id
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


async def test_search_and_filters_on_postgres(client: AsyncClient) -> None:
    headers = await _user_with_projects(
        "lister@example.com",
        [
            ("Equipment 予約", "detailed", "interviewing"),
            ("達成率100%", "simple", "completed"),
            ("達成率1000", "simple", "completed"),
            ("equipment 管理", "detailed", "completed"),
        ],
    )

    async def titles(params: dict) -> tuple[list[str], int]:
        body = (await client.get("/api/v1/projects", headers=headers, params=params)).json()
        return [item["title"] for item in body["items"]], body["total"]

    assert await titles({"q": "EQUIP"}) == (["Equipment 予約", "equipment 管理"], 2)
    assert await titles({"q": "100%"}) == (["達成率100%"], 1)
    assert await titles(
        {"mode": "detailed", "status": ["interviewing", "revising"]}
    ) == (["Equipment 予約"], 1)


async def test_pages_by_ten_on_postgres(client: AsyncClient) -> None:
    headers = await _user_with_projects(
        "pager@example.com", [(f"p{i:02d}", "simple", "interviewing") for i in range(12)]
    )

    second = (await client.get("/api/v1/projects", headers=headers, params={"page": 2})).json()

    assert [item["title"] for item in second["items"]] == ["p10", "p11"]
    assert second["total"] == 12
