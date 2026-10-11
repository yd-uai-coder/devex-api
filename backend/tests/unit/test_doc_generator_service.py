import uuid
from typing import Any

import pytest
import structlog.testing
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.external_design import external_design_output, upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.requirements import requirements_output

from app.documents.external_design import ExternalDesignModel
from app.documents.external_design.drafting import ExternalDesignGenerationOutput
from app.documents.requirements import RequirementsModel
from app.documents.requirements.drafting import RequirementsGenerationOutput
from app.hearing import PointState
from app.models.chat_history import ChatHistory
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import DOC_TYPES, GeneratedDocumentRepository
from app.repositories.hearing_point import HearingPointRepository
from app.services.doc_generator_service import (
    DocGeneratorService,
    _collapse_doubled_headings,
    _render_transcript,
    document_read,
)
from app.services.errors import (
    DocGenerationInProgressError,
    DocumentNotFoundError,
    RequirementNotAssumedError,
    RequirementNotFoundError,
)


async def _create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    return project


# 構造化出力で作る文書(要件定義書・外部設計書)。残りは ainvoke で md を返させる
_STRUCTURED_DOC_TYPES = ("requirements", "external_design")


def _fake_llm_for_generation(
    requirements: RequirementsGenerationOutput | None = None,
    external_design: ExternalDesignGenerationOutput | None = None,
) -> FakeLLM:
    # 要件定義書・外部設計書は構造化出力(各1回)、残りの2文書の生成 + 自己診断(1件) = 計3回の
    # ainvoke呼び出しを想定した順序のcontent。
    contents: list[str | list[str | dict[Any, Any]]] = [
        f"# {doc_type}\n内容" for doc_type in DOC_TYPES if doc_type not in _STRUCTURED_DOC_TYPES
    ]
    contents.append("## 自己診断\n最重要: 特になし")
    return FakeLLM(
        content_sequence=contents,
        structured_by_schema=upstream_outputs(requirements, external_design),
    )


async def test_generate_creates_all_four_documents(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=fake_llm)

    documents = await GeneratedDocumentRepository(db_session).list_current_for_project(project.id)
    assert {d.doc_type for d in documents} == set(DOC_TYPES)
    assert all(d.version == 1 for d in documents)


async def test_generate_records_self_diagnosis_as_others_chat_history(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=fake_llm)

    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    others = [h for h in history if h.sender == "others"]
    assert len(others) == 1
    assert "自己診断" in others[0].message


async def test_generate_transitions_status_to_completed(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=fake_llm)

    await db_session.refresh(project)
    assert project.status == "completed"


async def test_generate_logs_info_on_completion(db_session: AsyncSession) -> None:
    """主要ライフサイクルイベントとしてINFOログを記録する(内部設計書3.4節)。"""
    project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()
    service = DocGeneratorService(db_session)

    with structlog.testing.capture_logs() as logs:
        await service.generate(project.id, project.user_id, llm=fake_llm)

    info_logs = [log for log in logs if log["log_level"] == "info"]
    assert len(info_logs) == 1
    assert info_logs[0]["event"] == "documents_generated"
    assert info_logs[0]["project_id"] == str(project.id)
    assert info_logs[0]["doc_count"] == len(DOC_TYPES)


async def test_generate_reverts_status_and_records_failure_on_llm_error(
    db_session: AsyncSession,
) -> None:
    class _RaisingLLM:
        async def ainvoke(self, _messages: list) -> object:
            raise RuntimeError("quota exceeded")

    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)

    # 例外を送出しないことを確認
    await service.generate(project.id, project.user_id, llm=_RaisingLLM())

    await db_session.refresh(project)
    assert project.status == "interviewing"
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    others = [h for h in history if h.sender == "others"]
    assert len(others) == 1
    assert "生成に失敗しました" in others[0].message


async def test_generate_reverts_to_revising_when_regeneration_fails(
    db_session: AsyncSession,
) -> None:
    """revising(修正中)から再生成を試みて失敗した場合、interviewingではなくrevisingへ
    戻すことを確認する(生成済みだったという文脈を失わないため)。"""

    class _RaisingLLM:
        async def ainvoke(self, _messages: list) -> object:
            raise RuntimeError("quota exceeded")

    project = await _create_project(db_session)
    project.status = "revising"
    await db_session.commit()
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=_RaisingLLM())

    await db_session.refresh(project)
    assert project.status == "revising"


async def test_generate_from_revising_transitions_to_completed_on_success(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    project.status = "revising"
    await db_session.commit()
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=_fake_llm_for_generation())

    await db_session.refresh(project)
    assert project.status == "completed"


async def test_generate_on_missing_project_does_nothing(db_session: AsyncSession) -> None:
    service = DocGeneratorService(db_session)
    fake_llm = _fake_llm_for_generation()

    await service.generate(uuid.uuid4(), uuid.uuid4(), llm=fake_llm)  # 例外を送出しないことを確認

    assert fake_llm.invoke_messages == []  # LLMは一度も呼ばれない


async def test_generate_twice_adds_new_version_not_overwrite(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)
    await service.generate(project.id, project.user_id, llm=_fake_llm_for_generation())

    await service.generate(project.id, project.user_id, llm=_fake_llm_for_generation())

    documents = await GeneratedDocumentRepository(db_session).list_current_for_project(project.id)
    assert all(d.version == 2 for d in documents)


async def test_generate_extracts_text_from_thought_signature_content(
    db_session: AsyncSession,
) -> None:
    """Geminiがthought signature付きの応答(contentが辞書のリスト)を返しても、
    生成された文書・自己診断結果がrepr化されずプレーンテキストで保存されることを確認する。"""
    project = await _create_project(db_session)
    contents: list[str | list[str | dict[Any, Any]]] = [
        [{"type": "text", "text": f"# {doc_type}\n内容", "extras": {"signature": "sig"}}]
        for doc_type in DOC_TYPES
        if doc_type not in _STRUCTURED_DOC_TYPES
    ]
    contents.append([{"type": "text", "text": "## 自己診断\n最重要: 特になし"}])
    fake_llm = FakeLLM(content_sequence=contents, structured_by_schema=upstream_outputs())
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=fake_llm)

    documents = await GeneratedDocumentRepository(db_session).list_current_for_project(project.id)
    for document in documents:
        assert "{'type'" not in document.content
        assert "内容" in document.content
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    others = [h for h in history if h.sender == "others"]
    assert "{'type'" not in others[0].message
    assert "自己診断" in others[0].message


async def test_generate_chains_each_document_from_prior_confirmed_documents(
    db_session: AsyncSession,
) -> None:
    """requirementsのみチャット全履歴(と観点の表)を直接読み、それ以外は前段で確定済みの文書だけを
    入力にする(連鎖構成)ことを、実際にLLMへ渡されたHumanMessageの中身で確認する。
    external_designはrequirementsのみ、internal_designはrequirements+external_design、
    implementation_planはrequirements+internal_design(external_designは含めない)を
    入力にする、という参照関係を検証する。要件定義書・外部設計書は model から組み立てた md が
    後段に渡る(どちらも構造化出力)。"""
    project = await _create_project(db_session)
    contents = ["INTERNAL_DESIGN_マーカー", "IMPLEMENTATION_PLAN_マーカー", "自己診断結果"]
    fake_llm = FakeLLM(
        content_sequence=list(contents),
        structured_by_schema=upstream_outputs(
            requirements_output(text="REQUIREMENTS_マーカー"),
            external_design_output(overview="EXTERNAL_DESIGN_マーカー"),
        ),
    )
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=fake_llm)

    requirements_input = fake_llm.structured_messages[0][-1].content
    external_input = fake_llm.structured_messages[1][-1].content
    human_contents = [messages[-1].content for messages in fake_llm.invoke_messages]

    # requirements: チャット全履歴(transcript)を直接読む
    assert "備品予約システムを作りたい" in requirements_input

    # external_design: requirementsの生成結果のみ
    assert "REQUIREMENTS_マーカー" in external_input
    assert "備品予約システムを作りたい" not in external_input

    # internal_design: requirements + external_designの生成結果
    assert "REQUIREMENTS_マーカー" in human_contents[0]
    assert "EXTERNAL_DESIGN_マーカー" in human_contents[0]

    # implementation_plan: requirements + internal_designの生成結果(external_designは含めない)
    assert "REQUIREMENTS_マーカー" in human_contents[1]
    assert "INTERNAL_DESIGN_マーカー" in human_contents[1]
    assert "EXTERNAL_DESIGN_マーカー" not in human_contents[1]


async def _decide_points(session: AsyncSession, project: Project, **statuses: str) -> None:
    await HearingPointRepository(session).save_states(
        project.id, [PointState(k, s, f"{k}の決定") for k, s in statuses.items()]  # type: ignore[arg-type]
    )


async def test_generate_requirements_builds_model_from_points_table(
    db_session: AsyncSession,
) -> None:
    """要件定義書は構造化出力 → model → md。観点の表がプロンプトに入り、出どころは観点の状態を
    上限にする(セキュリティが未決なので、AI が user と書いても推測)。"""
    project = await _create_project(db_session)
    project.hearing_scale = "personal"
    await _decide_points(db_session, project, core="decided", security="open")
    fake_llm = _fake_llm_for_generation()

    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=fake_llm)

    assert fake_llm.structured_output_calls == [
        RequirementsGenerationOutput,
        ExternalDesignGenerationOutput,
    ]
    prompt = fake_llm.structured_messages[0][-1].content
    assert "core|コア機能|ユーザーが決定|coreの決定" in prompt
    assert "規模: 個人・小規模" in prompt
    document = await GeneratedDocumentRepository(db_session).get_current(
        project_id=project.id, doc_type="requirements"
    )
    assert document is not None and document.model is not None
    model = RequirementsModel.model_validate(document.model)
    assert [(r.id, r.source) for r in model.rows] == [("FR-001", "user"), ("NFR-001", "assumed")]
    assert "| NFR-001 | セキュリティ | HTTPS | **推測** | セキュリティ |" in document.content
    summary = document_read(document).requirements
    assert summary is not None
    assert summary.counts == {"user": 1, "adopted": 0, "assumed": 1}
    assert [a.id for a in summary.assumed] == ["NFR-001"]


async def test_regenerate_requirements_keeps_ids_through_prev_id(
    db_session: AsyncSession,
) -> None:
    """再生成では前の版の要件(ID 付き)をプロンプトに渡し、AI が指した prev_id を引き継ぐ。"""
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)
    await service.generate(project.id, project.user_id, llm=_fake_llm_for_generation())
    fake_llm = _fake_llm_for_generation(requirements_output(prev_id="FR-001", text="言い直し"))

    await service.generate(project.id, project.user_id, llm=fake_llm)

    assert "FR-001|備品を予約できる" in fake_llm.structured_messages[0][-1].content
    document = await GeneratedDocumentRepository(db_session).get_current(
        project_id=project.id, doc_type="requirements"
    )
    assert document is not None and document.version == 2
    model = RequirementsModel.model_validate(document.model)
    # FR-001 は引き継ぎ、prev_id の無い NFR は新しい番号(消えた NFR-001 は使い回さない)
    assert [r.id for r in model.rows] == ["FR-001", "NFR-002"]


async def test_generate_external_design_builds_model_linked_to_requirements(
    db_session: AsyncSession,
) -> None:
    """外部設計書は構造化出力 → model → md。入力は要件定義書の md(要件ID 付き)で、生成した
    要件定義書の要件ID を model に写し、文書の応答に行数と検証の指摘を付ける。"""
    project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()

    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=fake_llm)

    prompt = fake_llm.structured_messages[1][-1].content
    assert "## 前の版の画面\nなし" in prompt
    assert "| FR-001 |" in prompt  # 要件定義書の md(要件ID 付き)
    document = await GeneratedDocumentRepository(db_session).get_current(
        project_id=project.id, doc_type="external_design"
    )
    assert document is not None and document.model is not None
    model = ExternalDesignModel.model_validate(document.model)
    assert model.known_req_ids == ["FR-001", "NFR-001"]
    assert model.must_fr_ids == ["FR-001"]
    assert "## 2.6 API一覧" in document.content
    assert "| POST | /api/v1/reservations | 予約を登録する | SCR-001 | FR-001 |" in document.content
    read = document_read(document)
    assert read.requirements is None
    assert read.external_design is not None
    assert read.external_design.counts == {"screens": 1, "apis": 1, "integrations": 0}
    # 見本の POST は本文が空 → 中程度の指摘
    assert [i.code for i in read.external_design.issues] == ["API_BODY_MISSING"]


async def test_regenerate_external_design_keeps_screen_ids(db_session: AsyncSession) -> None:
    """再生成では前の版の画面(ID 付き)をプロンプトに渡し、前の版に在る ID だけを引き継ぐ。"""
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)
    await service.generate(project.id, project.user_id, llm=_fake_llm_for_generation())
    fake_llm = _fake_llm_for_generation(
        external_design=external_design_output(screen_id="SCR-007")
    )

    await service.generate(project.id, project.user_id, llm=fake_llm)

    assert "SCR-001|予約画面" in fake_llm.structured_messages[1][-1].content
    document = await GeneratedDocumentRepository(db_session).get_current(
        project_id=project.id, doc_type="external_design"
    )
    assert document is not None and document.version == 2
    model = ExternalDesignModel.model_validate(document.model)
    # 前の版に無い仮の ID は次の番号に振り直し、API の関連画面も付け替える
    assert [s.id for s in model.screens] == ["SCR-002"]
    assert model.apis[0].screens == ["SCR-002"]


async def test_adopt_requirement_rewrites_same_version(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)
    await service.generate(project.id, project.user_id, llm=_fake_llm_for_generation())

    document = await service.adopt_requirement(project, "NFR-001")

    assert document.version == 1  # 版は増やさない
    assert "| NFR-001 | セキュリティ | HTTPS | 採用 | セキュリティ |" in document.content
    model = RequirementsModel.model_validate(document.model)
    assert [r.source for r in model.rows] == ["assumed", "adopted"]  # FR は観点が未決で推測
    with pytest.raises(RequirementNotAssumedError):
        await service.adopt_requirement(project, "NFR-001")
    with pytest.raises(RequirementNotFoundError):
        await service.adopt_requirement(project, "FR-099")


async def test_adopt_requirement_rejects_missing_model_and_generating(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)
    with pytest.raises(DocumentNotFoundError):
        await service.adopt_requirement(project, "FR-001")
    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="requirements", content="以前の md"
    )
    with pytest.raises(RequirementNotFoundError):  # model の無い以前の版
        await service.adopt_requirement(project, "FR-001")
    project.status = "generating"
    with pytest.raises(DocGenerationInProgressError):
        await service.adopt_requirement(project, "FR-001")


async def test_document_read_has_no_summary_without_model(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    document = await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="requirements", content="以前の md"
    )

    assert document_read(document).requirements is None


async def test_list_versions_returns_newest_first(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)
    repo = GeneratedDocumentRepository(db_session)
    for content in ("v1", "v2", "v3"):
        await repo.create_version(project_id=project.id, doc_type="requirements", content=content)

    versions = await service.list_versions(project.id, "requirements")

    assert [v.content for v in versions] == ["v3", "v2", "v1"]


async def test_restore_version_switches_current_without_adding_version(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v2")

    restored = await service.restore_version(project.id, "requirements", version=1)

    assert restored.version == 1  # 版番号は増えない(新しい行を作らない)
    assert restored.content == "v1"
    versions = await repo.list_versions(project_id=project.id, doc_type="requirements")
    assert [(v.version, v.is_current) for v in versions] == [(2, False), (1, True)]
    current = await repo.get_current(project_id=project.id, doc_type="requirements")
    assert current is not None
    assert current.content == "v1"  # 一覧・ダウンロードが参照する表示中の版が切り替わる


async def test_restore_then_regenerate_makes_new_version_current(
    db_session: AsyncSession,
) -> None:
    """復元後に再生成(create_version)すると、その新しい版が表示中になる。"""
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v2")
    await service.restore_version(project.id, "requirements", version=1)

    await repo.create_version(project_id=project.id, doc_type="requirements", content="v3")

    versions = await repo.list_versions(project_id=project.id, doc_type="requirements")
    assert [(v.version, v.is_current) for v in versions] == [(3, True), (2, False), (1, False)]


async def test_restore_version_raises_for_missing_version(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)

    with pytest.raises(DocumentNotFoundError):
        await service.restore_version(project.id, "requirements", version=99)


async def test_list_current_documents_returns_current_versions_only(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()
    service = DocGeneratorService(db_session)
    await service.generate(project.id, project.user_id, llm=fake_llm)

    documents = await service.list_current_documents(project.id)

    assert {d.doc_type for d in documents} == set(DOC_TYPES)


async def test_get_document_returns_document_for_owning_project(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()
    service = DocGeneratorService(db_session)
    await service.generate(project.id, project.user_id, llm=fake_llm)
    documents = await service.list_current_documents(project.id)
    target = documents[0]

    result = await service.get_document(project=project, doc_id=target.id)

    assert result.id == target.id


async def test_get_document_raises_not_found_for_other_project(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    other_project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()
    service = DocGeneratorService(db_session)
    await service.generate(project.id, project.user_id, llm=fake_llm)
    documents = await service.list_current_documents(project.id)
    target = documents[0]

    with pytest.raises(DocumentNotFoundError):
        await service.get_document(project=other_project, doc_id=target.id)


async def test_get_document_raises_not_found_for_unknown_id(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)

    with pytest.raises(DocumentNotFoundError):
        await service.get_document(project=project, doc_id=uuid.uuid4())


def test_collapse_doubled_headings_keeps_the_first_marks() -> None:
    """LLM が重ねた見出しの記号は先頭だけを残す。本文中の「#」やコードは変えない。"""
    markdown = "## 4.3 開発環境\n\n### ### 1. ツール\n## ### 2. 構成\n- issue #12\n#タグ\n"

    assert _collapse_doubled_headings(markdown) == (
        "## 4.3 開発環境\n\n### 1. ツール\n## 2. 構成\n- issue #12\n#タグ\n"
    )


def test_render_transcript_excludes_others_sender() -> None:
    history = [
        ChatHistory(sender="user", message="要望A"),
        ChatHistory(sender="ai", message="応答A"),
        ChatHistory(sender="others", message="自己診断結果"),
    ]

    transcript = _render_transcript(history)

    assert "要望A" in transcript
    assert "応答A" in transcript
    assert "自己診断結果" not in transcript


def test_internal_design_prompt_asks_for_fixed_format_headings_for_uml_generation() -> None:
    """内部設計書プロンプトが
    テーブル見出しと処理別データフロー(DF見出し)の固定形式を指示していること。"""
    from app.services.doc_generator_service import _DOC_TYPE_PROMPTS

    prompt = _DOC_TYPE_PROMPTS["internal_design"]

    assert "### テーブル: <テーブル名>" in prompt
    assert "### 処理別データフロー" in prompt
    assert "#### DF-<連番>: <HTTPメソッド> <パス>" in prompt
    assert "元/データ/変換/先" in prompt


def test_implementation_plan_prompt_asks_for_vertical_units_with_ids() -> None:
    """実装計画書プロンプトが、WBSを縦割り・ID付きの決まった書式で書くよう指示していること
    (簡易モードの実装手順書が、この書式を作業単位として決定的に読むため)。"""
    from app.detailed_design.simple_procedure import parse_wbs
    from app.services.doc_generator_service import _DOC_TYPE_PROMPTS

    prompt = _DOC_TYPE_PROMPTS["implementation_plan"]

    assert "### M-<2桁の連番>: <マイルストーン名> ── 【Must/Should/Could】" in prompt
    assert "- [ ] M-<2桁>-T<2桁> [機能] <タスク名>" in prompt
    for field in (
        "処理: DF-<連番>",
        "要件: FR-<3桁>",
        "依存: M-<2桁>-T<2桁>",
        "モジュール: <パス>",
        "環境・設定:",
    ):
        assert field in prompt
    # 例の WBS も、決まった書式どおりに読める(要件の欄を含む)
    example = prompt.split("```\n")[1]
    tasks = parse_wbs("## 4.2 タスク分解（WBS）\n" + example).plan.milestones[0].tasks
    assert [task.req_ids for task in tasks] == [[], ["FR-001"]]
    assert "タスクに番号を付けない" not in prompt
