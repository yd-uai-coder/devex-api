"""E2E用の偽LLM(`E2eFakeLLM`)の、ヒアリング(観点の表・選択肢カード)から上流の2文書の model までの
契約テスト。

ブラウザの E2E(devex-ui の `e2e/devex-flow.spec.ts`)と同じ順に、作成 → 最初の発話(規模のカード)
→ カードに答えて送る → 文を送る → 十分 → 4文書の生成 → 推測の要件の「採用にする」を、画面を使わずに
サービスのメソッドで通す。偽LLM の出力が、本物の観点の取り込みの規則(根拠・利用者の発言だけが
決める観点)・完了判定・要件定義書と外部設計書の組み立てと検証を通ることを固定する。

SUT: E2eFakeLLM の判定・返信・2文書の構造化出力(app/ai/llm/fake.py)と、それを受ける
     ChatService・DocGeneratorService の経路
ドライバ: テスト関数(画面の操作と同じ順に、サービスのメソッドを呼ぶ)
スタブ: なし ── 偽LLM は検証される側(出力の出どころ)。DB はインメモリ SQLite(`db_session`)。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.fake import E2eFakeLLM
from app.hearing import POINT_DEFS, cards_from_json
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.hearing import CardAnswer
from app.services.chat_service import ChatService, ReplyCards
from app.services.doc_generator_service import DocGeneratorService, document_read
from app.services.project import ProjectService


async def _create_project(session: AsyncSession) -> Project:
    user = User(email="e2e-fake@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    return await ProjectService(session).create(
        user_id=user.id,
        name="在庫管理",
        intake={
            "system_overview": "在庫管理システムを作りたい",
            "goals_raw": "在庫数をリアルタイムに可視化したい",
            "notes_raw": None,
            "environment": None,
        },
        files=[],
    )


async def _send(
    chat: ChatService,
    project: Project,
    llm: E2eFakeLLM,
    message: str,
    answers: list[CardAnswer] | None = None,
) -> list:
    return [
        chunk
        async for chunk in chat.stream_reply(
            project, user_message=message, card_answers=answers or [], llm=llm
        )
    ]


async def test_e2e_fake_outputs_pass_hearing_and_upstream_documents(
    db_session: AsyncSession,
) -> None:
    llm = E2eFakeLLM()
    project = await _create_project(db_session)
    chat = ChatService(db_session)

    # 最初の発話には、コードが規模のカードを付ける(規模は初期入力から推し量らない)
    await chat.generate_opening_reply(project, llm=llm)
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    [scale_card] = cards_from_json(history[-1].cards)
    assert scale_card.point_key == "scale"
    assert project.hearing_scale is None

    # 規模のカードに答えて文も送る → 規模は利用者の発言を根拠に「採用」、返信は次の観点のカード
    answer = CardAnswer(card_id=scale_card.id, option=0)
    chunks = await _send(chat, project, llm, "利用者は倉庫の担当者です", [answer])
    assert isinstance(chunks[-1], ReplyCards)
    view = await chat.points_view(project)
    assert {p.key: p.status for p in view.points}["scale"] == "adopted"
    assert project.hearing_check is not None and project.hearing_check["is_sufficient"] is False

    # 文を2回送る → 偽の判定が全観点を決め、必須がそろって十分(まとめを返し、カードは無い)
    await _send(chat, project, llm, "MVPでは入出庫の記録と一覧だけ作ります")
    chunks = await _send(chat, project, llm, "Python と PostgreSQL で作ります")
    assert not any(isinstance(c, ReplyCards) for c in chunks)
    assert project.hearing_check["is_sufficient"] is True
    view = await chat.points_view(project)
    assert all(p.status == "decided" for p in view.points)
    assert len(view.points) == len(POINT_DEFS)
    assert all(p.evidence for p in view.points)

    # 4文書を生成する。要件定義書・外部設計書は model を持つ(指摘は推測の要件の軽微な1件だけ)
    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=llm)
    documents = GeneratedDocumentRepository(db_session)
    requirements = await documents.get_current(project_id=project.id, doc_type="requirements")
    external = await documents.get_current(project_id=project.id, doc_type="external_design")
    assert requirements is not None and requirements.model is not None
    assert external is not None and external.model is not None

    summary = document_read(requirements).requirements
    assert summary is not None
    # 推測は、偽物が assumed と書いたセキュリティの要件だけ。規模だけを根拠にした制約も、
    # 規模が決まっているので推測にならない
    assert [(r.id, r.kind) for r in summary.assumed] == [("NFR-002", "NFR")]
    assert summary.counts == {"user": 4, "adopted": 1, "assumed": 1}
    assert [i.code for i in summary.issues] == ["ASSUMED_REQUIREMENTS"]
    rows = {r["id"]: r for r in requirements.model["rows"]}
    assert (rows["CON-002"]["points"], rows["CON-002"]["source"]) == (["scale"], "user")

    design = document_read(external).external_design
    assert design is not None
    assert design.counts == {"screens": 1, "apis": 2, "integrations": 0}
    assert design.issues == []

    # プレビューの「採用にする」: 同じ版のまま、推測が採用に変わる
    adopted = await DocGeneratorService(db_session).adopt_requirement(project, "NFR-002")
    assert adopted.version == requirements.version
    summary = document_read(adopted).requirements
    assert summary is not None
    assert summary.counts == {"user": 4, "adopted": 2, "assumed": 0}
    assert (summary.assumed, summary.issues) == ([], [])
