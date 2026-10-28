import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from app.api.deps import CurrentProjectDep, CurrentUserDep, SessionDep
from app.api.responses import content_disposition
from app.change_requests import explanation_filename
from app.schemas.change_request import (
    ChangeRequestComparisonRead,
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestExplanationRead,
    ChangeRequestImpactRead,
    ChangeRequestRead,
    ChangeRequestReviewLink,
    ChangeRequestSummaryRead,
    ChangeRequestTraceRead,
    ChangeRequestUpdate,
    ChangeRequestVerificationRead,
    ChangeRequestVerify,
    ChangeRequestWithdraw,
    DocumentChecksUpdate,
    ExcludedItemRead,
    OptionDraftRead,
    ReviewRunBriefRead,
    SummaryEdit,
)
from app.schemas.share_link import (
    ChangeRequestCommentRead,
    ChangeRequestReplyCreate,
    ShareLinkCreate,
    ShareLinkCreatedRead,
    ShareLinkExtend,
    ShareLinkRead,
)
from app.services.change_request_service import (
    ChangeRequestService,
    ChangeRequestView,
    ExplanationView,
    OptionDraftView,
    VerificationView,
)
from app.services.share_link_service import ShareLinkService

# 束と同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/change-requests", tags=["change-requests"])

ChangeRequestNumber = Annotated[int, Path(ge=1, description="CR の番号(CR-1 なら 1)")]


def _explanation(view: ExplanationView) -> ChangeRequestExplanationRead:
    return ChangeRequestExplanationRead.build(
        view.change_request,
        view.explanation,
        output=view.output,
        summary=view.summary,
        summary_state=view.summary_state,
        baseline_freshness=view.baseline_freshness,
    )


def _option_draft(view: OptionDraftView) -> OptionDraftRead:
    return OptionDraftRead.build(
        view.draft, view.options, view.check, current_fingerprint=view.current_fingerprint
    )


def _verification(view: VerificationView) -> ChangeRequestVerificationRead:
    return ChangeRequestVerificationRead.build(
        view.change_request, view.verification, can_verify=view.can_verify
    )


def _read(view: ChangeRequestView) -> ChangeRequestRead:
    return ChangeRequestRead.from_view(view)


@router.post("", response_model=ChangeRequestRead, status_code=status.HTTP_201_CREATED)
async def create_change_request(
    payload: ChangeRequestCreate,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """CR を下書きで作る。基準の束は番号の束(省けば最新)。簡易ドキュメントモードと束が無いは409、
    基準の束に無い要件は422。"""
    view = await ChangeRequestService(session).create(
        current_project, payload, created_by=current_user.id
    )
    return _read(view)


@router.get("", response_model=list[ChangeRequestSummaryRead])
async def list_change_requests(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[ChangeRequestSummaryRead]:
    """プロジェクトの CR を新しい順に返す(中身と時系列は詳細で)。"""
    change_requests = await ChangeRequestService(session).list_for_project(current_project)
    return [ChangeRequestSummaryRead.from_model(c) for c in change_requests]


@router.get("/{number}", response_model=ChangeRequestRead)
async def get_change_request(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> ChangeRequestRead:
    """CR 1つ(中身・合意の記録・時系列・重なり・写しの元と先)。無ければ404。"""
    return _read(await ChangeRequestService(session).get(current_project, number))


@router.get("/{number}/impact", response_model=ChangeRequestImpactRead)
async def get_change_request_impact(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> ChangeRequestImpactRead:
    """CR の影響の予測(基準の束から、対象の要件ごとに見直しが要る要素)。判断の後は判断の時点で
    保存したもの。無ければ404。"""
    view = await ChangeRequestService(session).impact(current_project, number)
    return ChangeRequestImpactRead.build(
        view.change_request,
        view.prediction,
        saved=view.saved,
        excluded=[ExcludedItemRead(key=e.key, state=e.state) for e in view.excluded],
    )


@router.get("/{number}/comparison", response_model=ChangeRequestComparisonRead)
async def get_change_request_comparison(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> ChangeRequestComparisonRead:
    """反映した CR の、影響の予測と反映で付いた印の比較(一致 / 予測だけ / 実際だけ)と、文書への
    反映ごとの要素の前 → 後。無ければ404、まだ反映していなければ409。"""
    view = await ChangeRequestService(session).comparison(current_project, number)
    return ChangeRequestComparisonRead.build(
        view.change_request,
        view.comparison,
        saved=view.saved,
        scale=view.scale,
        reflections=view.reflections,
    )


@router.get("/{number}/trace", response_model=ChangeRequestTraceRead)
async def get_change_request_trace(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> ChangeRequestTraceRead:
    """CR の受付から検証までをたどる記録(時系列の行と、作った版・段階の版・結んだレビュー・
    入れた束)。無ければ404。"""
    view = await ChangeRequestService(session).trace(current_project, number)
    return ChangeRequestTraceRead.build(view.change_request, view.entries)


@router.get("/{number}/verification", response_model=ChangeRequestVerificationRead)
async def get_change_request_verification(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> ChangeRequestVerificationRead:
    """反映した CR の検証の確かめ(反映で付いた印の今の確認の状態と、文書の側の確認の一覧)。
    無ければ404、まだ反映していなければ409。"""
    return _verification(
        await ChangeRequestService(session).verification(current_project, number)
    )


@router.put("/{number}/document-checks", response_model=ChangeRequestVerificationRead)
async def check_change_request_documents(
    number: ChangeRequestNumber,
    payload: DocumentChecksUpdate,
    session: SessionDep,
    current_project: CurrentProjectDep,
) -> ChangeRequestVerificationRead:
    """文書の側の確認の項目を、確認済み(または未確認)にする。反映済みでなければ409。"""
    return _verification(
        await ChangeRequestService(session).check_documents(current_project, number, payload)
    )


@router.get("/{number}/explanation", response_model=ChangeRequestExplanationRead)
async def get_change_request_explanation(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> ChangeRequestExplanationRead:
    """CR の説明書のプレビュー(5節の md・出力する md・作業単位とテストの内訳・要約と照合)。
    無ければ404。"""
    return _explanation(await ChangeRequestService(session).explanation(current_project, number))


@router.get("/{number}/explanation/download")
async def download_change_request_explanation(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> Response:
    """CR の説明書を md でダウンロードする(`cr-<番号>-explanation.md`。確認済みで古くない要約が
    あれば載せる)。無ければ404。"""
    view = await ChangeRequestService(session).explanation(current_project, number)
    return Response(
        content=view.output,
        media_type="text/markdown",
        headers={"Content-Disposition": content_disposition(explanation_filename(number))},
    )


@router.post("/{number}/explanation/summary", response_model=ChangeRequestExplanationRead)
async def generate_change_request_summary(
    number: ChangeRequestNumber,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestExplanationRead:
    """説明書の5節から、LLM でクライアント向けの要約を作る(未確認。前の要約は上書き)。"""
    view = await ChangeRequestService(session).generate_summary(
        current_project, number, user_id=current_user.id
    )
    return _explanation(view)


@router.patch("/{number}/explanation/summary", response_model=ChangeRequestExplanationRead)
async def edit_change_request_summary(
    number: ChangeRequestNumber,
    payload: SummaryEdit,
    session: SessionDep,
    current_project: CurrentProjectDep,
) -> ChangeRequestExplanationRead:
    """要約の文を直す(未確認に戻る)。要約が無ければ404 `CR_SUMMARY_NOT_FOUND`。"""
    view = await ChangeRequestService(session).edit_summary(current_project, number, payload.text)
    return _explanation(view)


@router.post(
    "/{number}/explanation/summary/confirm", response_model=ChangeRequestExplanationRead
)
async def confirm_change_request_summary(
    number: ChangeRequestNumber,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestExplanationRead:
    """要約を確かめた(誰が・いつ)と記録し、出力に載せる。要約が無ければ404。"""
    view = await ChangeRequestService(session).confirm_summary(
        current_project, number, actor_id=current_user.id
    )
    return _explanation(view)


@router.post("/{number}/options/draft", response_model=OptionDraftRead)
async def generate_change_request_option_draft(
    number: ChangeRequestNumber,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> OptionDraftRead:
    """説明書から、LLM で選択肢(案)の下書きを作る(未確認。前の下書きは上書き)。下書き・説明済み
    だけで、ほかは409。"""
    view = await ChangeRequestService(session).generate_option_draft(
        current_project, number, user_id=current_user.id
    )
    return _option_draft(view)


@router.get("/{number}/options/draft", response_model=OptionDraftRead)
async def get_change_request_option_draft(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> OptionDraftRead:
    """選択肢の下書きと照合の結果。下書きが無ければ404 `CR_OPTION_DRAFT_NOT_FOUND`。"""
    return _option_draft(await ChangeRequestService(session).option_draft(current_project, number))


@router.post("/{number}/options/draft/adopt", response_model=ChangeRequestRead)
async def adopt_change_request_option_draft(
    number: ChangeRequestNumber,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """下書きの案を CR の案に取り込む(説明済みは下書きに戻る)。案の規則か CR の対象に合わなければ
    422 `CR_OPTION_INVALID`、下書きが無ければ404。"""
    view = await ChangeRequestService(session).adopt_option_draft(
        current_project, number, actor_id=current_user.id
    )
    return _read(view)


@router.patch("/{number}", response_model=ChangeRequestRead)
async def update_change_request(
    number: ChangeRequestNumber,
    payload: ChangeRequestUpdate,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """CR の中身を直す(下書き・説明済みだけ。説明済みは下書きに戻る)。合意の後は409。"""
    view = await ChangeRequestService(session).update(
        current_project, number, payload, actor_id=current_user.id
    )
    return _read(view)


@router.post("/{number}/explain", response_model=ChangeRequestRead)
async def explain_change_request(
    number: ChangeRequestNumber,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """下書きの CR を説明済みにする。下書きでなければ409。"""
    view = await ChangeRequestService(session).explain(
        current_project, number, actor_id=current_user.id
    )
    return _read(view)


@router.post("/{number}/decision", response_model=ChangeRequestRead)
async def decide_change_request(
    number: ChangeRequestNumber,
    payload: ChangeRequestDecision,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """説明済みの CR を合意か却下にし、合意の記録を残す。説明済みでなければ409。"""
    view = await ChangeRequestService(session).decide(
        current_project, number, payload, actor_id=current_user.id
    )
    return _read(view)


@router.post("/{number}/withdraw", response_model=ChangeRequestRead)
async def withdraw_change_request(
    number: ChangeRequestNumber,
    payload: ChangeRequestWithdraw,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """合意の前の CR を取り下げる。合意の後は409。"""
    view = await ChangeRequestService(session).withdraw(
        current_project, number, payload, actor_id=current_user.id
    )
    return _read(view)


@router.post(
    "/{number}/copy", response_model=ChangeRequestRead, status_code=status.HTTP_201_CREATED
)
async def copy_change_request(
    number: ChangeRequestNumber,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """却下した CR を写して、新しい番号の CR を下書きで作る。却下でなければ409。"""
    view = await ChangeRequestService(session).copy_rejected(
        current_project, number, created_by=current_user.id
    )
    return _read(view)


@router.post("/{number}/verify", response_model=ChangeRequestRead)
async def verify_change_request(
    number: ChangeRequestNumber,
    payload: ChangeRequestVerify,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """反映済みの CR を検証済みにする。反映済みでない・未確認が残っていれば409。"""
    view = await ChangeRequestService(session).verify(
        current_project, number, payload, actor_id=current_user.id
    )
    return _read(view)


@router.get("/{number}/review-candidates", response_model=list[ReviewRunBriefRead])
async def list_change_request_review_candidates(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> list[ReviewRunBriefRead]:
    """CR に結べる設計レビューの回(反映の後に流して成功し、まだ結んでいないもの。新しい順)。
    反映済みでなければ空。無ければ404。"""
    runs = await ChangeRequestService(session).review_candidates(current_project, number)
    return [ReviewRunBriefRead.from_run(run) for run in runs]


@router.post("/{number}/reviews", response_model=ChangeRequestRead)
async def link_change_request_review(
    number: ChangeRequestNumber,
    payload: ChangeRequestReviewLink,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """CR に設計レビューの回を結ぶ(時系列に1行)。回が無ければ404、結べなければ409。"""
    view = await ChangeRequestService(session).link_review(
        current_project, number, payload.review_run_id, actor_id=current_user.id
    )
    return _read(view)


@router.post("/{number}/conflict-checks", response_model=ChangeRequestRead)
async def check_change_request_conflicts(
    number: ChangeRequestNumber,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """重なる CR の競合(基準の束の後にほかの CR の反映で変わった対象の要件)を、今の文と見比べて
    確かめた、にする(時系列に1行)。競合が無ければ409。"""
    view = await ChangeRequestService(session).check_conflicts(
        current_project, number, actor_id=current_user.id
    )
    return _read(view)


@router.post(
    "/{number}/share-links",
    response_model=ShareLinkCreatedRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_change_request_share_link(
    number: ChangeRequestNumber,
    payload: ShareLinkCreate,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ShareLinkCreatedRead:
    """CR の共有リンクを作る(下書きは説明済みになる。有効なリンクがあれば取り消してから作る)。
    トークンはこの応答でだけ返す。下書き・説明済みでなければ409。"""
    created = await ShareLinkService(session).create(
        current_project, number, payload, actor_id=current_user.id
    )
    return ShareLinkCreatedRead.from_created(created)


@router.get("/{number}/share-links", response_model=list[ShareLinkRead])
async def list_change_request_share_links(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> list[ShareLinkRead]:
    """CR の共有リンク(新しい順。取り消したものも。トークンは含めない)。無ければ404。"""
    views = await ShareLinkService(session).list_for(current_project, number)
    return [ShareLinkRead.build(view) for view in views]


@router.post("/{number}/share-links/{link_id}/revoke", response_model=ShareLinkRead)
async def revoke_change_request_share_link(
    number: ChangeRequestNumber,
    link_id: uuid.UUID,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ShareLinkRead:
    """共有リンクを取り消す(取り消し済みなら何もしない)。リンクが無ければ404。"""
    view = await ShareLinkService(session).revoke(
        current_project, number, link_id, actor_id=current_user.id
    )
    return ShareLinkRead.build(view)


@router.post("/{number}/share-links/{link_id}/extend", response_model=ShareLinkRead)
async def extend_change_request_share_link(
    number: ChangeRequestNumber,
    link_id: uuid.UUID,
    payload: ShareLinkExtend,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ShareLinkRead:
    """共有リンクの期限を、今から指定の日数後に置き直す。取り消したリンクは409、無ければ404。"""
    view = await ShareLinkService(session).extend(
        current_project, number, link_id, payload, actor_id=current_user.id
    )
    return ShareLinkRead.build(view)


@router.get("/{number}/comments", response_model=list[ChangeRequestCommentRead])
async def list_change_request_comments(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> list[ChangeRequestCommentRead]:
    """CR のコメント欄(クライアントのコメントごとに返信をまとめ、古い順)。CR が無ければ404。"""
    threads = await ShareLinkService(session).comments_for(current_project, number)
    return [ChangeRequestCommentRead.from_thread(thread) for thread in threads]


@router.post(
    "/{number}/comments/{comment_id}/replies",
    response_model=ChangeRequestCommentRead,
    status_code=status.HTTP_201_CREATED,
)
async def reply_change_request_comment(
    number: ChangeRequestNumber,
    comment_id: uuid.UUID,
    payload: ChangeRequestReplyCreate,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestCommentRead:
    """クライアントのコメントに返信する(返したコメントと返信の一覧を返す)。コメントが無ければ404、
    返信への返信は409。"""
    thread = await ShareLinkService(session).reply(
        current_project, number, comment_id, payload, actor_id=current_user.id
    )
    return ChangeRequestCommentRead.from_thread(thread)
