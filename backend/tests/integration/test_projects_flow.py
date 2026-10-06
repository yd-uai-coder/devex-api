import pytest
from httpx import AsyncClient
from tests.fixtures.fake_llm import FakeLLM

from app.schemas.generation import HearingCompletionCheck
from app.services import chat_service as chat_service_module
from app.services import doc_generator_service as doc_generator_service_module

pytestmark = pytest.mark.integration

_EMAIL = "flow-checker@example.com"
_PASSWORD = "s3cret-pass"


async def _register_and_login(client: AsyncClient) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": _EMAIL, "password": _PASSWORD, "full_name": "Flow Checker"},
    )
    login_response = await client.post(
        "/api/v1/auth/login", json={"email": _EMAIL, "password": _PASSWORD}
    )
    return login_response.json()["access_token"]


def _install_fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """chat_service.py・doc_generator_service.pyがそれぞれ自前で呼ぶget_gemini_llm()を
    差し替える。両モジュールともgemini.pyからget_gemini_llmを名前でimportしているため、
    差し替えは呼び出し元モジュールの名前空間ごとに行う必要がある(モジュールが違えば
    別の名前束縛になるため、app.ai.llm.gemini側だけを差し替えても効かない)。

    E2eFakeLLM(環境変数E2E_FAKE_LLM経由、ブラウザE2E専用)とは別物 ──
    こちらはpytestプロセス内の関数差し替えであり、実プロセスを外部から叩くPlaywrightからは
    そもそも差し替えが届かない。
    """
    fake = FakeLLM(
        content="[Fake] 次に、想定している主なユーザー層を教えてください。",
        stream_chunks=["[Fake] 次に、", "想定している主なユーザー層を教えてください。"],
        structured_sequence=[
            HearingCompletionCheck(is_sufficient=True, summary="[Fake] 要約です", missing_points=[])
        ],
    )
    monkeypatch.setattr(chat_service_module, "get_gemini_llm", lambda: fake)
    monkeypatch.setattr(doc_generator_service_module, "get_gemini_llm", lambda: fake)
    return fake


async def test_full_projects_flow_create_chat_generate_download(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登録→プロジェクト作成→チャット送信→ヒアリング完了判定→生成トリガー→ドキュメント一覧→
    ダウンロードの一連をAPIレベルで検証する。ブラウザ経由のE2Eの前段として、
    「配線」自体がすべて繋がっていることを高速・決定論的に検証する(ブラウザ起動やSSE表示の
    描画待ちを伴わない分、失敗時の原因切り分けがしやすい)。"""
    _install_fake_llm(monkeypatch)
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    create_response = await client.post(
        "/api/v1/projects",
        headers=headers,
        data={
            "system_overview": "在庫管理システム",
            "goals_raw": "在庫をリアルタイムに可視化したい",
        },
        files=[],  # multipart/form-dataを強制する(filesパラメータ自体はFastAPI側のFile()に必須)
    )
    assert create_response.status_code == 201
    project_id = create_response.json()["id"]
    assert create_response.json()["status"] == "interviewing"

    # ヒアリング完了判定には最低発話数(ユーザー発話3件)が必要なため、3往復送る
    for message in (
        "利用者は倉庫の担当者を想定しています",
        "MVPでは在庫の入出庫記録と一覧表示のみ作ります",
        "技術的な制約は特にありません",
    ):
        chat_response = await client.post(
            f"/api/v1/projects/{project_id}/chat",
            headers=headers,
            json={"message": message},
        )
        assert chat_response.status_code == 200
    assert "text/event-stream" in chat_response.headers["content-type"]

    history_response = await client.get(f"/api/v1/projects/{project_id}/chat", headers=headers)
    assert history_response.status_code == 200
    senders = [entry["sender"] for entry in history_response.json()]
    # intake要約(サーバー起動時の最初のAI発話)・ユーザー発話・AI返信が揃っていること
    assert "user" in senders and "ai" in senders

    completion_response = await client.get(
        f"/api/v1/projects/{project_id}/hearing-completion", headers=headers
    )
    assert completion_response.status_code == 200
    assert completion_response.json()["is_sufficient"] is True

    generate_response = await client.post(
        f"/api/v1/projects/{project_id}/generate", headers=headers
    )
    assert generate_response.status_code == 202

    # BackgroundTasksはASGITransport経由のリクエストと同じイベントループ内で、レスポンスが
    # 返るまでに実行が完了する(Starletteの既定動作)。そのためポーリングや待機は不要。
    documents_response = await client.get(
        f"/api/v1/projects/{project_id}/documents", headers=headers
    )
    assert documents_response.status_code == 200
    documents = documents_response.json()
    doc_types = {d["doc_type"] for d in documents}
    assert doc_types == {
        "requirements",
        "external_design",
        "internal_design",
        "implementation_plan",
    }

    project_response = await client.get(f"/api/v1/projects/{project_id}", headers=headers)
    assert project_response.json()["status"] == "completed"

    doc_id = documents[0]["id"]
    download_response = await client.get(
        f"/api/v1/projects/{project_id}/documents/{doc_id}/download", headers=headers
    )
    assert download_response.status_code == 200
    assert download_response.headers["content-type"].startswith("text/markdown")
    # FakeLLMは固定の応答文字列のみ返すため、生成内容そのものの品質はここでは検証しない
    # (品質はdoc_generator_serviceの単体テスト・プロンプト設計の責務)。
    assert download_response.text == "[Fake] 次に、想定している主なユーザー層を教えてください。"


async def test_revising_status_after_message_on_completed_project(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成完了(completed)後にチャットへ新規メッセージを送ると、revising(修正中)へ遷移することを
    APIレベルで確認する(app/services/chat_service.py ChatService.stream_reply参照)。"""
    _install_fake_llm(monkeypatch)
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    create_response = await client.post(
        "/api/v1/projects",
        headers=headers,
        data={"system_overview": "勤怠管理システム", "goals_raw": "打刻を簡略化したい"},
        files=[],
    )
    project_id = create_response.json()["id"]

    await client.post(f"/api/v1/projects/{project_id}/generate", headers=headers)
    project_response = await client.get(f"/api/v1/projects/{project_id}", headers=headers)
    assert project_response.json()["status"] == "completed"

    await client.post(
        f"/api/v1/projects/{project_id}/chat",
        headers=headers,
        json={"message": "追加の要望があります"},
    )
    project_response = await client.get(f"/api/v1/projects/{project_id}", headers=headers)
    assert project_response.json()["status"] == "revising"
