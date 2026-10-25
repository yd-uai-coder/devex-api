from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import ClientIpDep, RedisDep, SessionDep
from app.schemas.share_link import (
    SharedChangeRequestRead,
    SharedCommentCreate,
    SharedCommentRead,
    SharedDecisionCreate,
)
from app.services.share_link_service import ShareLinkService
from app.services.share_rate_limit import ShareRateLimiter

# ログインの要らない入口(クライアントが共有リンクで CR を読み、コメント・合意 / 却下をする)。
# 認証の依存は付けない
router = APIRouter(prefix="/share", tags=["share"])

ShareToken = Annotated[str, Path(min_length=1, max_length=128, description="共有リンクのトークン")]


@router.get("/{token}", response_model=SharedChangeRequestRead)
async def get_shared_change_request(
    token: ShareToken, session: SessionDep, redis: RedisDep, client_ip: ClientIpDep
) -> SharedChangeRequestRead:
    """共有リンクの CR(説明書・選択肢・判断の結果。直している・閉じた CR は状態と題だけ)。
    リンクが無ければ404 `SHARE_LINK_NOT_FOUND`、期限切れ・取り消しは410、回数の上限は429。"""
    await ShareRateLimiter(redis).enforce_view(ip=client_ip)
    view = await ShareLinkService(session).view(token)
    return SharedChangeRequestRead.build(view)


@router.post(
    "/{token}/comments", response_model=SharedCommentRead, status_code=status.HTTP_201_CREATED
)
async def comment_on_shared_change_request(
    token: ShareToken,
    payload: SharedCommentCreate,
    session: SessionDep,
    redis: RedisDep,
    client_ip: ClientIpDep,
) -> SharedCommentRead:
    """共有リンクからコメントする(名前・本文・見た説明書の版)。開いていないリンクは409
    `SHARE_LINK_NOT_WRITABLE`、見た版が古ければ409 `SHARE_EXPLANATION_CHANGED`。リンクが無ければ
    404、期限切れ・取り消しは410、書き込みの回数の上限は429。"""
    await ShareRateLimiter(redis).enforce_write(ip=client_ip)
    thread = await ShareLinkService(session).comment(token, payload)
    return SharedCommentRead.from_thread(thread)


@router.post("/{token}/decision", response_model=SharedChangeRequestRead)
async def decide_shared_change_request(
    token: ShareToken,
    payload: SharedDecisionCreate,
    session: SessionDep,
    redis: RedisDep,
    client_ip: ClientIpDep,
) -> SharedChangeRequestRead:
    """共有リンクから合意・却下する(名前・見た説明書の版・案のある CR の合意では案)。判断の後の
    閲覧の形を返す。開いていないリンク(先に開発者が判断したときも)は409
    `SHARE_LINK_NOT_WRITABLE`、見た版が古ければ409 `SHARE_EXPLANATION_CHANGED`、理由の無い却下は422
    `SHARE_DECISION_REASON_REQUIRED`、案の選び方の誤りは422 `CR_OPTION_INVALID`。リンクが無ければ
    404、期限切れ・取り消しは410、書き込みの回数の上限は429。"""
    await ShareRateLimiter(redis).enforce_write(ip=client_ip)
    view = await ShareLinkService(session).decide(token, payload)
    return SharedChangeRequestRead.build(view)
