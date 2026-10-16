"""プロジェクトの物理削除と、バックグラウンドの処理がプロジェクトの消失を見分ける部品。

削除の要求(`ProjectService.delete`)は印(`projects.deleted_at`)を付けるだけで返す。生成中のジョブは
LLM を呼ぶ間もトランザクションを開いたままで、子の行を書いた後は親の行に外部キーのロックを持つため、
物理削除はロックが空いたときに行う(`purge_deleted_projects`。削除の直後と、各ジョブの終わり)。

ジョブは、入口・失敗の記録の前に `project_vanished` で確かめ、成功・失敗の記録の commit は
`commit_if_live` を通す。消えていれば、結果も失敗の記録も残さずに終える。
"""

import uuid
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.database import AsyncSessionLocal
from app.repositories.project import ProjectRepository

logger = structlog.get_logger(__name__)

# プロジェクトが途中で物理削除されたときに、書き込みで出る例外(子の行の外部キーの違反・行の無い更新)
VANISHED_ERRORS = (IntegrityError, StaleDataError)

# 物理削除で子の行のロックを待つ上限。超えたら次の機会に回す(削除の要求やジョブを長く止めない)
_PURGE_LOCK_TIMEOUT = "3s"


async def project_vanished(session: AsyncSession, project_id: uuid.UUID, *, job: str) -> bool:
    """プロジェクトが消えた(行が無い・削除の印がある)か。消えていればログを残す。"""
    if await ProjectRepository(session).is_live(project_id):
        return False
    logger.info("project_deleted_during_job", project_id=str(project_id), job=job)
    return True


async def commit_if_live(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    job: str,
    write: Callable[[], Awaitable[None]] | None = None,
) -> bool:
    """プロジェクトが生きていれば、`write`(書き込み)を行って commit し、True を返す。
    消えていれば rollback して False を返す(確かめた時点で消えていたときと、書き込み・commit で
    消失の例外が出たとき)。結果も失敗の記録も残さない。確かめてから commit するまでに削除の印が
    付いたときは、書き込みは印の付いたプロジェクトに入り、後の物理削除で消える。"""
    if await project_vanished(session, project_id, job=job):
        await session.rollback()
        return False
    try:
        if write is not None:
            await write()
        await session.commit()
    except VANISHED_ERRORS:
        await session.rollback()
        if await project_vanished(session, project_id, job=job):
            return False
        raise
    return True


async def purge_deleted(session: AsyncSession) -> int:
    """削除の印が付いた行を、ロックが取れるものだけ物理削除して commit する。消した数を返す。
    子の行のロックを待ちきれなかったときは rollback して 0 を返す(印は残り、次の機会に消える)。"""
    try:
        if session.get_bind().dialect.name == "postgresql":
            await session.execute(text(f"SET LOCAL lock_timeout = '{_PURGE_LOCK_TIMEOUT}'"))
        count = await ProjectRepository(session).purge_deleted()
        await session.commit()
    except DBAPIError as exc:
        await session.rollback()
        logger.info("project_purge_postponed", error_type=type(exc.orig).__name__)
        return 0
    if count:
        logger.info("projects_purged", count=count)
    return count


async def purge_deleted_projects() -> None:
    """`BackgroundTasks`・ジョブの終わりから呼ぶ物理削除(セッションは自前で開く)。
    失敗してもログだけにする(印が残っていれば、次の機会に消える)。"""
    try:
        async with AsyncSessionLocal() as session:
            await purge_deleted(session)
    except Exception as exc:  # noqa: BLE001 -- 物理削除の失敗で呼び出し元を止めない
        logger.warning("project_purge_failed", error_type=type(exc).__name__)
