from typing import Annotated

from fastapi import APIRouter, Path

from app.api.deps import ClientIpDep, RedisDep, SessionDep
from app.schemas.share_link import SharedChangeRequestRead
from app.services.share_link_service import ShareLinkService
from app.services.share_rate_limit import ShareRateLimiter

# ログインの要らない入口(クライアントが共有リンクで CR を読む)。認証の依存は付けない
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
