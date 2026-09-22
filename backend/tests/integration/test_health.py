import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_health_reports_database_and_redis_ok(client: AsyncClient) -> None:
    """実際のDB/Redisに接続できている状態で、/healthがokを返すことを確認する。"""
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["redis"] == "ok"
