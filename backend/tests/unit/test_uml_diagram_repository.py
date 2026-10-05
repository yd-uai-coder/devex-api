import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.repositories.uml_diagram import UmlDiagramRepository


async def _create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="備品予約システム")
    session.add(project)
    await session.flush()
    return project


async def test_create_persists_diagram_with_draft_defaults(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = UmlDiagramRepository(db_session)

    diagram = await repo.create(
        project_id=project.id,
        view="structure",
        notation="component",
        semantic_model={"notation": "component", "elements": [], "relations": []},
    )

    assert diagram.status == "draft"
    assert diagram.version == 1


async def test_get_by_id_returns_none_for_other_projects_diagram(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    other_project = await _create_project(db_session)
    repo = UmlDiagramRepository(db_session)
    diagram = await repo.create(
        project_id=project.id,
        view="structure",
        notation="component",
        semantic_model={"notation": "component", "elements": [], "relations": []},
    )

    result = await repo.get_by_id(diagram.id, project_id=other_project.id)

    assert result is None


async def test_list_for_project_orders_by_updated_at_desc(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = UmlDiagramRepository(db_session)
    first = await repo.create(
        project_id=project.id,
        view="structure",
        notation="component",
        semantic_model={"notation": "component", "elements": [], "relations": []},
    )
    second = await repo.create(
        project_id=project.id,
        view="data",
        notation="er",
        semantic_model={"notation": "er", "elements": [], "relations": []},
    )
    # SQLiteのfunc.now()は秒単位の解像度のため、直後に連続作成した2件のupdated_atが
    # 同一になりうる。並び順を確定させるため明示的にずらす。
    first.updated_at = datetime.now(UTC) - timedelta(seconds=10)
    second.updated_at = datetime.now(UTC)
    await db_session.flush()

    result = await repo.list_for_project(project.id)

    assert [d.id for d in result] == [second.id, first.id]


async def test_get_by_subject_finds_diagram_by_notation_and_subject(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    repo = UmlDiagramRepository(db_session)
    diagram = await repo.create(
        project_id=project.id,
        view="dataflow",
        notation="dfd",
        semantic_model={"notation": "dfd", "elements": [], "relations": []},
        subject="POST /api/v1/reservations",
    )

    found = await repo.get_by_subject(
        project_id=project.id, notation="dfd", subject="POST /api/v1/reservations"
    )
    missing = await repo.get_by_subject(project_id=project.id, notation="dfd", subject="other")

    assert found is not None and found.id == diagram.id
    assert missing is None


async def test_list_by_notation(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = UmlDiagramRepository(db_session)
    await repo.create(
        project_id=project.id,
        view="structure",
        notation="component",
        semantic_model={"notation": "component", "elements": [], "relations": []},
    )
    await repo.create(
        project_id=project.id,
        view="dataflow",
        notation="dfd",
        semantic_model={"notation": "dfd", "elements": [], "relations": []},
        subject="GET /api/v1/reservations",
    )

    assert [d.notation for d in await repo.list_by_notation(project.id, "dfd")] == ["dfd"]
