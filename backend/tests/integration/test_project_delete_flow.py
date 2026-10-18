"""プロジェクトの削除を実際の PostgreSQL で確かめる(外部キーの CASCADE とロック)。

SUT: DELETE /api/v1/projects/{id}(ルート → ProjectService.delete → BackgroundTasks の
     purge_deleted_projects)と purge_deleted_projects
ドライバ: httpx の AsyncClient(ASGITransport。BackgroundTasks は応答を返す処理の中で走る)と、
     生成中のジョブの代わりにロックを持つ別のセッション
スタブ不要 ── 外部キーの CASCADE とロックの振る舞いそのものが検証対象のため、実際の DB を使う
(LLM は呼ばない。利用者・プロジェクト・子の行はセッションで直接作り、アクセストークンも
直接発行する ── API で登録すると、スイート全体で登録・ログインの IP ごとの上限に当たるため)。
"""

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.chat_history import ChatHistory
from app.models.project import Project
from app.models.user import User
from app.services.project_deletion import purge_deleted_projects

pytestmark = pytest.mark.integration

# 一覧の応答の形(Phase 65 で配列から {items, total} に変えた)で、何も無いとき
_EMPTY_PAGE = {"items": [], "total": 0}

async def _login(_client: AsyncClient, email: str) -> tuple[dict[str, str], uuid.UUID]:
    async with AsyncSessionLocal() as session:
        user = User(email=email, hashed_password="x")
        session.add(user)
        await session.commit()
        user_id = user.id
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}, user_id


async def _project_with_chat(user_id: uuid.UUID) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        project = Project(user_id=user_id, title="備品予約")
        session.add(project)
        await session.flush()
        session.add(ChatHistory(project_id=project.id, sender="user", message="こんにちは"))
        await session.commit()
        return project.id


async def _counts(project_id: uuid.UUID) -> tuple[int, int]:
    async with AsyncSessionLocal() as session:
        projects = await session.scalar(
            select(func.count()).select_from(Project).where(Project.id == project_id)
        )
        chats = await session.scalar(
            select(func.count())
            .select_from(ChatHistory)
            .where(ChatHistory.project_id == project_id)
        )
        return projects or 0, chats or 0


async def test_delete_removes_project_and_children(client: AsyncClient) -> None:
    headers, user_id = await _login(client, "deleter@example.com")
    project_id = await _project_with_chat(user_id)

    response = await client.delete(f"/api/v1/projects/{project_id}", headers=headers)

    assert response.status_code == 204
    assert (await client.get(f"/api/v1/projects/{project_id}", headers=headers)).status_code == 404
    assert (await client.get("/api/v1/projects", headers=headers)).json() == _EMPTY_PAGE
    assert await _counts(project_id) == (0, 0)  # 子の行は CASCADE で消える


async def test_other_user_gets_404(client: AsyncClient) -> None:
    _, owner_id = await _login(client, "owner@example.com")
    other_headers, _ = await _login(client, "other@example.com")
    project_id = await _project_with_chat(owner_id)

    response = await client.delete(f"/api/v1/projects/{project_id}", headers=other_headers)

    assert response.status_code == 404
    assert await _counts(project_id) == (1, 1)


async def test_delete_does_not_wait_for_a_job_holding_the_lock(client: AsyncClient) -> None:
    headers, user_id = await _login(client, "busy@example.com")
    project_id = await _project_with_chat(user_id)

    # 生成中のジョブの代わりに、子の行を書いてコミットしないセッション(親の行に FOR KEY SHARE)
    async with AsyncSessionLocal() as job:
        job.add(ChatHistory(project_id=project_id, sender="others", message="生成の途中"))
        await job.flush()

        response = await asyncio.wait_for(
            client.delete(f"/api/v1/projects/{project_id}", headers=headers), timeout=10
        )

        assert response.status_code == 204
        assert (await client.get("/api/v1/projects", headers=headers)).json() == _EMPTY_PAGE
        assert (await _counts(project_id))[0] == 1  # ロック中の行は物理削除を飛ばす
        await job.commit()

    await purge_deleted_projects()  # ジョブの終わりの物理削除

    assert await _counts(project_id) == (0, 0)


async def test_purge_postpones_when_a_child_row_stays_locked(client: AsyncClient) -> None:
    _, user_id = await _login(client, "locked@example.com")
    project_id = await _project_with_chat(user_id)
    async with AsyncSessionLocal() as session:  # 削除の印だけを付ける(物理削除はまだ)
        project = await session.get(Project, project_id)
        assert project is not None
        project.deleted_at = func.now()
        await session.commit()

    # 子の行だけを更新して持つ(親の行はロックしない)と、物理削除は子の行のロックで待つ
    async with AsyncSessionLocal() as job:
        chat = await job.scalar(select(ChatHistory).where(ChatHistory.project_id == project_id))
        assert chat is not None
        chat.message = "更新中"
        await job.flush()

        await asyncio.wait_for(purge_deleted_projects(), timeout=10)  # lock_timeout で諦める

        assert await _counts(project_id) == (1, 1)
        await job.rollback()

    await purge_deleted_projects()

    assert await _counts(project_id) == (0, 0)
