"""資料(議事録・メール・資料)の追加・一覧・取得・削除のテスト。

SUT: MaterialService(create_from_text / create_from_file / list_for_project / get / delete)・
     MaterialRepository、ルート create_material / upload_material / list_materials /
     get_material / delete_material、MaterialCreate、モデル Material と Project.materials、
     intake_file_processor.file_type_of
ドライバ: pytest のテスト関数(ルートは関数を直接呼ぶ)
スタブ: PDF を読む AI(LlmResolver.resolve_pdf_reader)を FakeLLM を返すものに差し替える。それ以外は
     スタブ不要 ── 永続化と上限の判定が検証対象のため、テスト用の SQLite のセッションを使う
"""

import io
import uuid
from datetime import date

import pytest
from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project
from tests.fixtures.fake_llm import FakeLLM

from app.ai.llm.catalog import Provider
from app.api.routes.materials import (
    create_material,
    delete_material,
    get_material,
    list_materials,
    upload_material,
)
from app.models.chat_history import ChatHistory
from app.models.material import Material
from app.models.project import Project
from app.models.user import User
from app.repositories.material import MaterialRepository
from app.schemas.material import MaterialCreate, MaterialMeta
from app.services import material_service
from app.services.errors import (
    DesignStagesNotAvailableError,
    FileTooLargeError,
    LlmUnavailableError,
    MaterialNotFoundError,
    MaterialTooLongError,
    MaterialUnreadableError,
    TooManyMaterialsError,
    UnsupportedFileTypeError,
)
from app.services.intake_file_processor import file_type_of
from app.services.llm_resolver import LlmResolver, ResolvedLlm
from app.services.material_service import MAX_MATERIAL_CHARS, MaterialService

_DATE = date(2026, 10, 20)


def _paste(**overrides: object) -> MaterialCreate:
    values: dict[str, object] = {
        "kind": "minutes",
        "title": "  第3回 定例  ",
        "material_date": _DATE,
        "body": "  予約の取消は前日までとする。  ",
    }
    values.update(overrides)
    return MaterialCreate.model_validate(values)


_META = MaterialMeta(kind="email", title="先方からのメール", material_date=_DATE)


async def _user(session: AsyncSession, project: Project) -> User:
    user = await session.get(User, project.user_id)
    assert user is not None
    return user


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


def _pdf_reader(monkeypatch: pytest.MonkeyPatch, *, content: str = "", error: bool = False) -> list:
    called: list = []

    async def _reader(_self: LlmResolver, user_id: uuid.UUID) -> ResolvedLlm:
        called.append(user_id)
        if error:
            raise LlmUnavailableError("キーが無い")
        return ResolvedLlm(
            llm=FakeLLM(content=content),
            provider=Provider.GEMINI,
            model="gemini-3.5-flash-lite",
            own_key=False,
        )

    monkeypatch.setattr(LlmResolver, "resolve_pdf_reader", _reader)
    return called


async def test_paste_route_creates_and_reads_back(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    user = await _user(db_session, project)

    created = await create_material(_paste(), db_session, project, user)

    assert (created.kind, created.title, created.source, created.filename) == (
        "minutes",
        "第3回 定例",
        "paste",
        None,
    )
    assert (created.body, created.length, created.created_by) == (
        "予約の取消は前日までとする。",
        len("予約の取消は前日までとする。"),
        user.id,
    )
    read = await get_material(created.id, db_session, project)
    assert read.body == created.body
    # ヒアリングの根拠(チャットの履歴)には入れない
    assert (await db_session.scalars(select(ChatHistory))).all() == []
    await db_session.refresh(project, ["materials"])
    assert [m.id for m in project.materials] == [created.id]


async def test_list_is_newest_first_without_body(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    user = await _user(db_session, project)
    first = await create_material(_paste(title="1回目"), db_session, project, user)
    second = await create_material(_paste(title="2回目", body="あいう"), db_session, project, user)

    listed = await list_materials(db_session, project)

    assert [(m.id, m.length) for m in listed] == [(second.id, 3), (first.id, first.length)]
    assert not hasattr(listed[0], "body")


async def test_txt_upload_keeps_text_without_truncation(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    user = await _user(db_session, project)
    text = "あ" * MAX_MATERIAL_CHARS

    created = await upload_material(
        db_session, project, user, _upload("mail.TXT", text.encode()), "email", "メール", _DATE
    )

    assert (created.source, created.filename, created.length) == ("txt", "mail.TXT", len(text))


async def test_pdf_is_read_by_owners_gemini(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_detailed_project(db_session)
    called = _pdf_reader(monkeypatch, content="PDFの本文")

    view = await MaterialService(db_session).create_from_file(
        project, _META, filename="資料.pdf", data=b"%PDF-1.4 ...", created_by=None
    )

    assert called == [project.user_id]
    assert (view.material.source, view.material.body, view.length) == ("pdf", "PDFの本文", 6)


@pytest.mark.parametrize(
    ("filename", "data", "error"),
    [
        ("資料.docx", b"x", UnsupportedFileTypeError),
        ("資料.txt", b"a" * (material_service.MAX_MATERIAL_FILE_BYTES + 1), FileTooLargeError),
        ("資料.txt", b"\xff\xfe\x00\x01", MaterialUnreadableError),
        ("資料.md", b"   \n", MaterialUnreadableError),
        ("資料.txt", ("あ" * (MAX_MATERIAL_CHARS + 1)).encode(), MaterialTooLongError),
    ],
)
async def test_file_upload_rejects_without_saving(
    db_session: AsyncSession, filename: str, data: bytes, error: type[Exception]
) -> None:
    project = await create_detailed_project(db_session)

    with pytest.raises(error):
        await MaterialService(db_session).create_from_file(
            project, _META, filename=filename, data=data, created_by=None
        )

    assert await MaterialRepository(db_session).count(project_id=project.id) == 0


async def test_pdf_without_reader_is_unreadable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_detailed_project(db_session)
    _pdf_reader(monkeypatch, error=True)

    with pytest.raises(MaterialUnreadableError, match="PDFを読むAIを使えませんでした"):
        await MaterialService(db_session).create_from_file(
            project, _META, filename="資料.pdf", data=b"%PDF", created_by=None
        )


async def test_pasted_body_over_limit_is_rejected(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)

    with pytest.raises(MaterialTooLongError):
        await MaterialService(db_session).create_from_text(
            project, _paste(body="あ" * (MAX_MATERIAL_CHARS + 1)), created_by=None
        )


async def test_count_limit(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(material_service, "MAX_MATERIALS_PER_PROJECT", 2)
    project = await create_detailed_project(db_session)
    service = MaterialService(db_session)
    for _ in range(2):
        await service.create_from_text(project, _paste(), created_by=None)

    with pytest.raises(TooManyMaterialsError):
        await service.create_from_text(project, _paste(), created_by=None)
    # 数の上限は、ファイルを読む前に確かめる(PDF を読む AI を呼ばない)
    with pytest.raises(TooManyMaterialsError):
        await service.create_from_file(
            project, _META, filename="a.pdf", data=b"%PDF", created_by=None
        )


async def test_delete_and_other_projects_material_is_not_found(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    other = await create_detailed_project(db_session)
    user = await _user(db_session, project)
    created = await create_material(_paste(), db_session, project, user)

    with pytest.raises(MaterialNotFoundError):
        await get_material(created.id, db_session, other)
    with pytest.raises(MaterialNotFoundError):
        await delete_material(created.id, db_session, other)

    await delete_material(created.id, db_session, project)

    assert (await db_session.scalars(select(Material))).all() == []
    with pytest.raises(MaterialNotFoundError):
        await get_material(created.id, db_session, project)


async def test_simple_mode_has_no_materials(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session, mode="simple", with_documents=False)
    service = MaterialService(db_session)

    with pytest.raises(DesignStagesNotAvailableError):
        await service.create_from_text(project, _paste(), created_by=None)
    with pytest.raises(DesignStagesNotAvailableError):
        await service.list_for_project(project)


def test_schema_rejects_blank_title_and_body() -> None:
    with pytest.raises(ValidationError):
        _paste(title="   ")
    with pytest.raises(ValidationError):
        _paste(body=" \n ")
    with pytest.raises(ValidationError):
        _paste(kind="memo")


def test_file_type_of() -> None:
    assert [file_type_of(n) for n in ("a.PDF", "b.tar.md", "noext")] == ["pdf", "md", ""]
