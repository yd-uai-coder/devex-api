import uuid

import pytest
import structlog.testing
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_template import PromptTemplate
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.intake_file import IntakeFileRepository
from app.services.errors import (
    FileTooLargeError,
    InvalidProjectNameError,
    PromptTemplateNotFoundError,
    TooManyFilesError,
    UnsupportedFileTypeError,
)
from app.services.project import (
    MAX_FILE_SIZE_BYTES,
    PROJECT_NAME_MAX_LENGTH,
    ProjectService,
    UploadedFileInput,
    _format_intake_summary,
)


async def _create_user(session: AsyncSession) -> User:
    user = User(email="owner@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    return user


async def test_create_persists_intake_as_chat_history(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    service = ProjectService(db_session)

    project = await service.create(
        name="備品予約",
        user_id=user.id,
        intake={"system_overview": "備品予約を一元管理したい", "goals_raw": "重複を防ぎたい"},
        files=[],
    )

    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert len(history) == 1
    assert history[0].sender == "intake"
    assert (
        history[0].message == "システム概要：備品予約を一元管理したい\n実現したい事：重複を防ぎたい"
    )


def test_format_intake_summary_omits_environment_and_null_notes() -> None:
    text = _format_intake_summary(
        {
            "system_overview": "トレンド情報を調べる",
            "goals_raw": "参考資料に記載",
            "notes_raw": None,
            "environment": {"languages": ["Python"]},
        },
        [],
    )

    assert text == "システム概要：トレンド情報を調べる\n実現したい事：参考資料に記載"
    assert "environment" not in text
    assert "null" not in text


def test_format_intake_summary_includes_notes_raw_when_present() -> None:
    text = _format_intake_summary(
        {"system_overview": "s", "goals_raw": "g", "notes_raw": "予算は限られている"}, []
    )

    assert text == "システム概要：s\n実現したい事：g\nその他備考：予算は限られている"


def test_format_intake_summary_includes_filenames_when_present() -> None:
    text = _format_intake_summary(
        {"system_overview": "s", "goals_raw": "g"}, ["a.pdf", "b.txt"]
    )

    assert text == "システム概要：s\n実現したい事：g\n添付ファイル：a.pdf、b.txt"


async def test_create_uses_stripped_name_as_title(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    service = ProjectService(db_session)

    project = await service.create(
        name="  備品予約  ",
        user_id=user.id, intake={"system_overview": "備品予約を一元管理するシステム"}, files=[]
    )

    assert project.title == "備品予約"


async def test_create_accepts_name_of_max_length(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    name = "あ" * PROJECT_NAME_MAX_LENGTH

    project = await ProjectService(db_session).create(
        name=name, user_id=user.id, intake={"system_overview": "s"}, files=[]
    )

    assert project.title == name


@pytest.mark.parametrize("name", ["", "   ", "あ" * (PROJECT_NAME_MAX_LENGTH + 1)])
async def test_create_rejects_empty_or_too_long_name(db_session: AsyncSession, name: str) -> None:
    user = await _create_user(db_session)

    with pytest.raises(InvalidProjectNameError):
        await ProjectService(db_session).create(
            name=name, user_id=user.id, intake={"system_overview": "s"}, files=[]
        )


async def test_create_ingests_txt_file_and_appends_chat_history(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    service = ProjectService(db_session)
    file = UploadedFileInput(filename="memo.txt", data="既存Excel管理からの移行".encode())

    project = await service.create(
        name="備品予約", user_id=user.id, intake={"system_overview": "s"}, files=[file]
    )

    intake_files = await IntakeFileRepository(db_session).list_for_project(project.id)
    assert len(intake_files) == 1
    assert intake_files[0].status == "processed"
    assert intake_files[0].extracted_text == "既存Excel管理からの移行"

    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    # ファイル名はintake要約行(表示対象)に、抽出テキストはsender='attachment'行
    # (チャット画面には表示しないが、ヒアリング・ドキュメント生成のLLMコンテキストには使う)
    # に分かれる
    assert [h.sender for h in history] == ["intake", "attachment"]
    assert "memo.txt" in history[0].message
    assert "既存Excel管理からの移行" in history[1].message


async def test_create_rejects_more_than_three_files(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    service = ProjectService(db_session)
    files = [UploadedFileInput(filename=f"f{i}.txt", data=b"x") for i in range(4)]

    with pytest.raises(TooManyFilesError):
        await service.create(
            name="備品予約", user_id=user.id, intake={"system_overview": "s"}, files=files
        )


async def test_create_rejects_unsupported_file_type(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    service = ProjectService(db_session)
    file = UploadedFileInput(filename="spec.docx", data=b"x")

    with pytest.raises(UnsupportedFileTypeError):
        await service.create(
            name="備品予約", user_id=user.id, intake={"system_overview": "s"}, files=[file]
        )


async def test_create_rejects_oversized_file(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    service = ProjectService(db_session)
    file = UploadedFileInput(filename="big.txt", data=b"x" * (MAX_FILE_SIZE_BYTES + 1))

    with pytest.raises(FileTooLargeError):
        await service.create(
            name="備品予約", user_id=user.id, intake={"system_overview": "s"}, files=[file]
        )


async def test_create_records_failed_status_without_raising(db_session: AsyncSession) -> None:
    # UTF-8として不正なバイト列を .txt として送った場合、例外は送出せず failed として記録する
    user = await _create_user(db_session)
    service = ProjectService(db_session)
    file = UploadedFileInput(filename="broken.txt", data=b"\xff\xfe\x00\x01")

    project = await service.create(
        name="備品予約", user_id=user.id, intake={"system_overview": "s"}, files=[file]
    )

    intake_files = await IntakeFileRepository(db_session).list_for_project(project.id)
    assert intake_files[0].status == "failed"
    assert intake_files[0].extracted_text is None

async def test_create_persists_valid_template_id(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    template = PromptTemplate(name="Webアプリケーション標準", target_type="Web", system_prompt="x")
    db_session.add(template)
    await db_session.flush()
    service = ProjectService(db_session)

    project = await service.create(
        name="備品予約",
        user_id=user.id, intake={"system_overview": "s"}, files=[], template_id=template.id
    )
    assert project.template_id == template.id


async def test_create_rejects_unknown_template_id(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    service = ProjectService(db_session)

    with pytest.raises(PromptTemplateNotFoundError):
        await service.create(
            name="備品予約",
            user_id=user.id, intake={"system_overview": "s"}, files=[], template_id=uuid.uuid4()
        )

async def test_list_for_user_returns_only_that_users_projects(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    other_user = User(email="other@example.com", hashed_password="x")
    db_session.add(other_user)
    await db_session.flush()
    service = ProjectService(db_session)
    await service.create(name="自分", user_id=user.id, intake={"system_overview": "s"}, files=[])
    await service.create(
        name="他人", user_id=other_user.id, intake={"system_overview": "other"}, files=[]
    )

    result = await service.list_for_user(user.id)

    assert len(result) == 1
    assert result[0].title == "自分"


async def test_get_detail_includes_intake_files(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    service = ProjectService(db_session)
    file = UploadedFileInput(filename="memo.txt", data="既存Excel管理からの移行".encode())
    project = await service.create(
        name="備品予約", user_id=user.id, intake={"system_overview": "s"}, files=[file]
    )

    detail = await service.get_detail(project)

    assert detail.id == project.id
    assert len(detail.intake_files) == 1
    assert detail.intake_files[0].filename == "memo.txt"


async def test_create_logs_info_with_project_id_only(db_session: AsyncSession) -> None:
    """主要ライフサイクルイベントとしてINFOログを記録する。system_overview等の本文は
    含めない(project_idのみ、内部設計書3.4節)。"""
    user = await _create_user(db_session)
    service = ProjectService(db_session)

    with structlog.testing.capture_logs() as logs:
        project = await service.create(
            name="備品予約",
            user_id=user.id, intake={"system_overview": "秘密の新規事業案"}, files=[]
        )

    info_logs = [log for log in logs if log["log_level"] == "info"]
    assert len(info_logs) == 1
    assert info_logs[0]["event"] == "project_created"
    assert info_logs[0]["project_id"] == str(project.id)
    assert "秘密の新規事業案" not in str(info_logs[0])
