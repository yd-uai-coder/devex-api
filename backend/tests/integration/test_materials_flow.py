"""資料の API を実際の PostgreSQL で確かめる(multipart の受け取り・本文の文字数・CASCADE・
誤りの応答)。

SUT: POST /api/v1/projects/{id}/materials(貼り付け)・POST .../materials/upload(ファイル)・
     GET 一覧・GET 1件・DELETE(ルート → MaterialService → MaterialRepository)と、プロジェクトの
     削除での資料の CASCADE
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ不要 ── LLM を呼ばない(txt / md だけ。PDF はユニットテストで偽の AI を使う)。利用者・
プロジェクトはセッションで直接作り、アクセストークンも直接発行する
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.material import Material
from app.models.project import Project
from app.models.user import User

pytestmark = pytest.mark.integration

_FORM = {"kind": "minutes", "title": "第3回 定例", "material_date": "2026-10-20"}


async def _login(email: str) -> tuple[dict[str, str], uuid.UUID]:
    async with AsyncSessionLocal() as session:
        user = User(email=email, hashed_password="x")
        session.add(user)
        await session.commit()
        user_id = user.id
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}, user_id


async def _project(user_id: uuid.UUID, *, mode: str = "detailed") -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        project = Project(user_id=user_id, title="備品予約", mode=mode, status="completed")
        session.add(project)
        await session.commit()
        return project.id


async def _material_rows(project_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count()).select_from(Material).where(Material.project_id == project_id)
        )
        return count or 0


async def test_paste_upload_list_get_delete_and_cascade(client: AsyncClient) -> None:
    headers, user_id = await _login("material-owner@example.com")
    project_id = await _project(user_id)
    base = f"/api/v1/projects/{project_id}/materials"

    body = {**_FORM, "body": "取消は前日までとする。"}
    pasted = await client.post(base, json=body, headers=headers)
    assert pasted.status_code == 201
    assert (pasted.json()["source"], pasted.json()["length"]) == ("paste", 11)

    uploaded = await client.post(
        f"{base}/upload",
        data={**_FORM, "kind": "email", "title": "メール"},
        files={"file": ("mail.md", "# 件名\n本文です".encode(), "text/markdown")},
        headers=headers,
    )
    assert uploaded.status_code == 201
    assert (uploaded.json()["source"], uploaded.json()["filename"]) == ("md", "mail.md")

    listed = await client.get(base, headers=headers)
    assert [m["title"] for m in listed.json()] == ["メール", "第3回 定例"]
    assert "body" not in listed.json()[0]
    assert listed.json()[0]["length"] == len("# 件名\n本文です")

    one = await client.get(f"{base}/{pasted.json()['id']}", headers=headers)
    assert one.json()["body"] == "取消は前日までとする。"

    deleted = await client.delete(f"{base}/{pasted.json()['id']}", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get(f"{base}/{pasted.json()['id']}", headers=headers)).status_code == 404
    assert await _material_rows(project_id) == 1

    removed = await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert removed.status_code == 204
    assert await _material_rows(project_id) == 0


async def test_errors(client: AsyncClient) -> None:
    headers, user_id = await _login("material-errors@example.com")
    project_id = await _project(user_id)
    base = f"/api/v1/projects/{project_id}/materials"

    too_long = await client.post(base, json={**_FORM, "body": "あ" * 20_001}, headers=headers)
    assert (too_long.status_code, too_long.json()["code"]) == (422, "MATERIAL_TOO_LONG")

    docx = await client.post(
        f"{base}/upload", data=_FORM, files={"file": ("a.docx", b"x")}, headers=headers
    )
    assert (docx.status_code, docx.json()["code"]) == (400, "UNSUPPORTED_FILE_TYPE")

    blank_title = await client.post(
        f"{base}/upload",
        data={**_FORM, "title": "  "},
        files={"file": ("a.txt", b"x")},
        headers=headers,
    )
    assert blank_title.status_code == 422

    other_headers, other_id = await _login("material-other@example.com")
    assert (await client.get(base, headers=other_headers)).status_code == 404

    simple_id = await _project(other_id, mode="simple")
    simple = await client.get(f"/api/v1/projects/{simple_id}/materials", headers=other_headers)
    assert simple.status_code == 409
