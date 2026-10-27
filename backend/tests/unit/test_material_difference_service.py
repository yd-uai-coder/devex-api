"""資料と最新の束の違いの取り出し・一覧・捨てる / 戻すのテスト。

SUT: MaterialDifferenceService(extract / list_for / dismiss / restore)・
     MaterialDifferenceRepository、
     ルート extract_material_differences / list_material_differences / update_material_difference、
     MaterialDifferenceUpdate・MaterialDifferencesRead、モデル MaterialExtraction・
     MaterialDifference と Material.differences、偽の LLM(E2eFakeLLM)の違いの取り出し
ドライバ: pytest のテスト関数(ルートは関数を直接呼ぶ)
スタブ: 取り出しの LLM を、決まった構造化出力を返す FakeLLM に差し替えてサービスに注入する
     (ルートのテストは、解決〈LlmResolver.resolve〉を資料の印から違いを返す E2eFakeLLM に
     差し替える)。束・CR は偽の LLM で反映する fixture
     (tests/fixtures/change_requests.py)で作り、DB はテスト用の SQLite
"""

import uuid
from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.change_requests import (
    agreed_change_request_project,
    applied_change_request_project,
)
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.llm_resolver import stub_llm_resolver

from app.ai.llm.catalog import Purpose
from app.ai.llm.fake import E2eFakeLLM
from app.api.routes.materials import (
    extract_material_differences,
    list_material_differences,
    update_material_difference,
)
from app.materials import DifferenceItemOut, DifferenceListOut
from app.models.material import Material
from app.models.material_difference import MaterialDifference, MaterialExtraction
from app.models.project import Project
from app.models.user import User
from app.repositories.material_difference import MaterialDifferenceRepository
from app.schemas.material import MaterialCreate
from app.schemas.material_difference import MaterialDifferenceUpdate
from app.services.errors import (
    MaterialBaselineMissingError,
    MaterialDifferenceNotFoundError,
    MaterialDifferenceStateError,
    MaterialNotFoundError,
)
from app.services.material_difference_service import MaterialDifferenceService
from app.services.material_service import MaterialService

_BODY = """# 第4回 定例
- 予約の登録の後に、確認メールを送ってほしい。
- 予約の一覧を見る機能は、今回はやめることにした。
- 取消の期限は次回までに決める。
"""


def _out(*items: dict[str, str]) -> DifferenceListOut:
    return DifferenceListOut(differences=[DifferenceItemOut.model_validate(i) for i in items])


_CHANGE = {
    "kind": "change",
    "target_key": "FR-001",
    "content": "登録の後に確認メールを送る",
    "quote": "予約の登録の後に、確認メールを送ってほしい。",
}
_DELETE = {
    "kind": "delete",
    "target_key": "req:FR-002",
    "content": "一覧をやめる",
    "quote": "予約の一覧を見る機能は、今回はやめることにした。",
}
_QUESTION = {
    "kind": "question",
    "content": "取消の期限が未定",
    "quote": "取消の期限は次回までに決める。",
}
_INVENTED = {"kind": "change", "target_key": "FR-009", "content": "x", "quote": "電話で知らせる"}


async def _material(session: AsyncSession, project: Project, body: str = _BODY) -> uuid.UUID:
    payload = MaterialCreate(
        kind="minutes", title="第4回 定例", material_date=date(2026, 10, 27), body=body
    )
    view = await MaterialService(session).create_from_text(project, payload, created_by=None)
    return view.material.id


def _service(session: AsyncSession, *outs: DifferenceListOut) -> MaterialDifferenceService:
    return MaterialDifferenceService(session, llm=FakeLLM(structured_sequence=list(outs)))


async def test_extract_checks_differences_and_records_stale_baseline(
    db_session: AsyncSession,
) -> None:
    project = await applied_change_request_project(db_session)
    material_id = await _material(db_session, project)
    service = _service(db_session, _out(_CHANGE, _DELETE, _QUESTION, _INVENTED))

    view = await service.extract(project, material_id, user_id=project.user_id)

    rows = [(d.kind, d.target_key, d.status, d.reasons) for d in view.differences]
    assert rows == [
        ("change", "req:FR-001", "unconfirmed", []),
        ("delete", "req:FR-002", "unconfirmed", []),
        ("question", "", "unconfirmed", []),
        ("change", "FR-009", "rejected", ["引用が資料に無い", "対象 FR-009 が束に無い"]),
    ]
    assert [d.position for d in view.differences] == [1, 2, 3, 4]
    assert {d.baseline_number for d in view.differences} == {1}
    # 束 B-1 の後に CR-1 を反映したので、比べた束は古い(止めずに記録する)
    assert view.extraction is not None
    assert view.extraction.baseline_number == 1
    assert view.extraction.freshness["fresh"] is False
    assert view.extraction.freshness["applied_after"][0]["number"] == 1
    assert view.latest_baseline_number == 1


async def test_rerun_replaces_unconfirmed_and_keeps_dismissed(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)
    material_id = await _material(db_session, project)
    service = _service(db_session, _out(_CHANGE, _DELETE, _INVENTED), _out(_CHANGE, _QUESTION))
    first = await service.extract(project, material_id, user_id=project.user_id)
    change = first.differences[0]
    await service.dismiss(project, material_id, change.id, reason="同じ会で撤回", actor_id=None)

    second = await service.extract(project, material_id, user_id=project.user_id)

    # 捨てた変更は残り、同じ違いは足し直さない。未確認の削除と弾いたものは置き換わる
    assert [(d.kind, d.status, d.position) for d in second.differences] == [
        ("change", "dismissed", 1),
        ("question", "unconfirmed", 2),
    ]
    assert second.extraction is not None
    assert second.extraction.freshness["fresh"] is True
    rows = await MaterialDifferenceRepository(db_session).list_for(material_id)
    assert len(rows) == 2


async def test_dismiss_and_restore_follow_states(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)
    material_id = await _material(db_session, project)
    service = _service(db_session, _out(_QUESTION, _INVENTED))
    view = await service.extract(project, material_id, user_id=project.user_id)
    question, rejected = view.differences

    closed = await service.dismiss(
        project, material_id, question.id, reason="次回の定例で確かめる", actor_id=project.user_id
    )
    assert (closed.status, closed.dismiss_reason, closed.decided_by) == (
        "dismissed",
        "次回の定例で確かめる",
        project.user_id,
    )
    with pytest.raises(MaterialDifferenceStateError):
        await service.dismiss(project, material_id, question.id, reason="x", actor_id=None)
    with pytest.raises(MaterialDifferenceStateError):
        await service.dismiss(project, material_id, rejected.id, reason="x", actor_id=None)
    with pytest.raises(MaterialDifferenceStateError):
        await service.restore(project, material_id, rejected.id)

    restored = await service.restore(project, material_id, question.id)
    assert (restored.status, restored.dismiss_reason, restored.decided_at) == (
        "unconfirmed",
        None,
        None,
    )
    with pytest.raises(MaterialDifferenceNotFoundError):
        await service.restore(project, material_id, uuid.uuid4())


async def test_without_baseline_or_material_is_refused(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    material_id = await _material(db_session, project)
    service = _service(db_session, _out(_CHANGE))

    with pytest.raises(MaterialBaselineMissingError):
        await service.extract(project, material_id, user_id=project.user_id)
    with pytest.raises(MaterialNotFoundError):
        await service.list_for(project, uuid.uuid4())

    view = await service.list_for(project, material_id)
    assert (view.extraction, view.latest_baseline_number, view.differences) == (None, None, [])


async def test_routes_with_fake_llm_marks_and_cascade(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await agreed_change_request_project(db_session)
    body = (
        "- 予約の登録の後に、確認メールを送ってほしい。[違い:change:req:FR-001]\n"
        "- 予約の画面に、空き状況の表を足してほしい。[違い:add:screen:SCR-001]\n"
        "- 取消の期限は次回までに決める。[違い:question:-]\n"
    )
    material_id = await _material(db_session, project, body)
    user = await db_session.get(User, project.user_id)
    assert user is not None
    # ルートは利用者の生成の用途で LLM を解決する。資料の印から違いを返す偽の LLM にする
    stub = stub_llm_resolver(monkeypatch, E2eFakeLLM())
    extracted = await extract_material_differences(material_id, db_session, project, user)

    assert stub.calls == [(user.id, Purpose.GENERATION)]
    assert [(d.kind, d.target_key, d.origin_key, d.status) for d in extracted.differences] == [
        ("change", "req:FR-001", "", "unconfirmed"),
        ("add", "", "screen:SCR-001", "unconfirmed"),
        ("question", "", "", "unconfirmed"),
    ]
    assert extracted.differences[0].quote == "予約の登録の後に、確認メールを送ってほしい。"
    assert extracted.extraction is not None
    assert (extracted.extraction.baseline_label, extracted.extraction.freshness.fresh) == (
        "B-1",
        True,
    )

    dismissed = await update_material_difference(
        material_id,
        extracted.differences[2].id,
        MaterialDifferenceUpdate(action="dismiss", reason="  次回に確かめる  "),
        db_session,
        project,
        user,
    )
    assert (dismissed.status, dismissed.dismiss_reason) == ("dismissed", "次回に確かめる")
    listed = await list_material_differences(material_id, db_session, project)
    assert [d.status for d in listed.differences] == ["unconfirmed", "unconfirmed", "dismissed"]

    material = await db_session.get(Material, material_id)
    assert material is not None
    await db_session.refresh(material, ["differences", "extraction"])
    assert len(material.differences) == 3 and material.extraction is not None
    await MaterialService(db_session).delete(project, material_id)
    for model in (MaterialDifference, MaterialExtraction):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0


def test_dismiss_needs_reason() -> None:
    with pytest.raises(ValidationError):
        MaterialDifferenceUpdate(action="dismiss")
    with pytest.raises(ValidationError):
        MaterialDifferenceUpdate(action="dismiss", reason="   ")
    assert MaterialDifferenceUpdate(action="restore").reason is None
