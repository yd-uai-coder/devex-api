import pytest
from httpx import AsyncClient
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM

from app.hearing import POINT_DEFS
from app.schemas.hearing import HearingPointsJudgement, HearingReplyOut, JudgedPointOut
from app.services import chat_service as chat_service_module
from app.services import doc_generator_service as doc_generator_service_module

pytestmark = pytest.mark.integration

_EMAIL = "flow-checker@example.com"
_PASSWORD = "s3cret-pass"


async def _register_and_login(client: AsyncClient) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": _EMAIL, "password": _PASSWORD, "user_name": "Flow Checker"},
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
        # ヒアリングの返信(本文と選択肢カードの構造化出力)。規模が未決の間は、コードが規模の
        # カードを足す
        structured_by_schema={
            HearingReplyOut: HearingReplyOut(
                message="[Fake] 次に、想定している主なユーザー層を教えてください。"
            ),
            # 要件定義書・外部設計書の下書き(構造化出力 → model → md)
            **upstream_outputs(),
        },
        # 観点の判定は作成時に1回と、文を送るたびに1回呼ばれる(下のテストは3往復)。
        # 2往復目までは目的だけ、
        # 3往復目で全観点が決まる(発言の番号: 初期入力=1、利用者の発言=2〜4)
        structured_sequence=[
            HearingPointsJudgement(),  # プロジェクト作成時の判定(初期入力からは何も決まらない)
            *[
                HearingPointsJudgement(
                    points=[
                        JudgedPointOut(
                            key="purpose", status="decided", summary="在庫の可視化", evidence=[1]
                        )
                    ]
                )
                for _ in range(2)
            ],
            HearingPointsJudgement(
                scale="internal",
                points=[
                    JudgedPointOut(
                        key=d.key, status="decided", summary=f"[Fake] {d.label}", evidence=[4]
                    )
                    for d in POINT_DEFS
                ],
            ),
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
            "name": "在庫管理",
            "system_overview": "在庫管理システム",
            "goals_raw": "在庫をリアルタイムに可視化したい",
        },
        files=[],  # multipart/form-dataを強制する(filesパラメータ自体はFastAPI側のFile()に必須)
    )
    assert create_response.status_code == 201
    project_id = create_response.json()["id"]
    assert create_response.json()["status"] == "interviewing"

    # 判定は送信のたびに返信の前に行われ、GETはその結果を返す(2往復目までは必須の観点が足りない)。
    # 2往復目は、1往復目の返信に付いた規模のカードを選んで送る
    chat_responses = []
    for index, message in enumerate(
        (
            "利用者は倉庫の担当者を想定しています",
            "MVPでは在庫の入出庫記録と一覧表示のみ作ります",
            "技術的な制約は特にありません",
        ),
        start=1,
    ):
        body: dict = {"message": message}
        if index == 2:
            history = await client.get(f"/api/v1/projects/{project_id}/chat", headers=headers)
            card = history.json()[-1]["cards"][0]
            assert card["point_key"] == "scale"
            body["card_answers"] = [{"card_id": card["id"], "option": 1}]
        chat_response = await client.post(
            f"/api/v1/projects/{project_id}/chat",
            headers=headers,
            json=body,
        )
        assert chat_response.status_code == 200
        chat_responses.append(chat_response)
        if index == 1:
            assert "event: cards" in chat_response.text
        if index == 2:
            points = await client.get(
                f"/api/v1/projects/{project_id}/hearing-points", headers=headers
            )
            assert points.json()["scale"] == "internal"
            assert points.json()["points"][0]["status"] == "adopted"
        if index < 3:
            pending = await client.get(
                f"/api/v1/projects/{project_id}/hearing-completion", headers=headers
            )
            assert pending.json()["is_sufficient"] is False
    assert "text/event-stream" in chat_responses[-1].headers["content-type"]
    # 十分になった送信の返信は、観点の表から組み立てたまとめ(LLMの返信ではない)
    assert "規模: 社内" in chat_responses[-1].text
    assert "[Fake] 可用性" in chat_responses[-1].text

    points_response = await client.get(
        f"/api/v1/projects/{project_id}/hearing-points", headers=headers
    )
    assert points_response.status_code == 200
    assert points_response.json()["is_sufficient"] is True
    assert points_response.json()["points"][0]["evidence"][0]["label"] == "発言3"

    history_response = await client.get(f"/api/v1/projects/{project_id}/chat", headers=headers)
    assert history_response.status_code == 200
    senders = [entry["sender"] for entry in history_response.json()]
    # intake要約(サーバー起動時の最初のAI発話)・ユーザー発話・AI返信が揃っていること
    assert "user" in senders and "ai" in senders
    # カードの選択は発言の本文に入り、カードには選ばれた選択肢が残る
    answered = next(e for e in history_response.json() if e["message"].startswith("【規模】"))
    assert answered["message"].startswith("【規模】社内の人が使う\n\n")
    assert any(
        card["selected"] == 1 for e in history_response.json() for card in e["cards"] or []
    )

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

    requirements = next(d for d in documents if d["doc_type"] == "requirements")
    # 観点はすべて決まっているので、要件の出どころはどちらもユーザー(推測なし)
    assert requirements["requirements"]["counts"] == {"user": 2, "adopted": 0, "assumed": 0}
    download_response = await client.get(
        f"/api/v1/projects/{project_id}/documents/{requirements['id']}/download", headers=headers
    )
    assert download_response.status_code == 200
    assert download_response.headers["content-type"].startswith("text/markdown")
    # 要件定義書は model から組み立てた md(要件ID と出どころの付いた表)
    assert "| FR-001 | Must | 備品を予約できる | ユーザー | コア機能 |" in download_response.text


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
        data={
            "name": "勤怠管理",
            "system_overview": "勤怠管理システム",
            "goals_raw": "打刻を簡略化したい",
        },
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


async def test_opening_scale_card_and_marking_point_not_applicable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最初の発話に規模のカードが付き、観点の表から未決の観点を対象外にできる(LLMは呼ばない)。"""
    _install_fake_llm(monkeypatch)
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    create_response = await client.post(
        "/api/v1/projects",
        headers=headers,
        data={"name": "備品予約", "system_overview": "備品予約", "goals_raw": "重複を防ぐ"},
        files=[],
    )
    project_id = create_response.json()["id"]

    history = await client.get(f"/api/v1/projects/{project_id}/chat", headers=headers)
    opening = history.json()[-1]
    assert opening["sender"] == "ai"
    assert [card["point_key"] for card in opening["cards"]] == ["scale"]

    url = f"/api/v1/projects/{project_id}/hearing-points"
    skip = {"status": "not_applicable"}
    response = await client.put(f"{url}/purpose", headers=headers, json=skip)
    assert response.status_code == 200
    purpose = next(p for p in response.json()["points"] if p["key"] == "purpose")
    assert purpose["status"] == "not_applicable"
    again = await client.put(f"{url}/purpose", headers=headers, json=skip)
    assert again.status_code == 409
    unknown = await client.put(f"{url}/unknown", headers=headers, json=skip)
    assert unknown.status_code == 404
    invalid = await client.put(f"{url}/users", headers=headers, json={"status": "decided"})
    assert invalid.status_code == 422


@pytest.mark.parametrize("name", ["   ", "あ" * 41])
async def test_create_project_rejects_blank_or_too_long_name(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """プロジェクト名は前後の空白を除いて1〜40文字(空白だけ・41文字は400。
    欄そのものが空なら、FastAPIのフォームの必須チェックで422になる)。"""
    _install_fake_llm(monkeypatch)
    token = await _register_and_login(client)

    response = await client.post(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {token}"},
        data={"name": name, "system_overview": "在庫管理システム", "goals_raw": "可視化したい"},
        files=[],
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PROJECT_NAME"
