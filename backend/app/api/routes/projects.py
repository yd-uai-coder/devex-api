import json
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile, status
from fastapi.responses import  Response, StreamingResponse

from app.api.deps import CurrentProjectDep, CurrentUserDep, SessionDep
from app.core.errors import BadRequestError
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.intake_file import IntakeFileRepository
from app.repositories.project import ProjectRepository
from app.schemas.document import GeneratedDocumentRead
from app.schemas.generation import HearingCompletionCheck
from app.schemas.hearing import ChatHistoryRead, HearingMessageRequest
from app.schemas.project import IntakeFileRead, ProjectDetail, ProjectRead
from app.services.chat_service import ChatService
from app.services.doc_generator_service import generate_documents
from app.services.errors import DocumentNotFoundError, GenerationFailedError, LLMQuotaExceededError
from app.services.project import ProjectService, UploadedFileInput

router = APIRouter(prefix="/projects", tags=["projects"])

def _parse_environment(raw: str | None) -> dict | None:
    """environmentフォームフィールド(JSON文字列)をdictへ変換する。未入力ならNoneを返す。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BadRequestError("environment must be a valid JSON object") from exc

def _content_disposition(filename: str) -> str:
    """日本語等の非ASCII文字を含むファイル名用のContent-Disposition値を組み立てる(RFC 5987)。
    ASCII非対応のクライアント向けにfilename(置換フォールバック)とfilename*(UTF-8)の両方を含める。"""
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    session: SessionDep,
    current_user: CurrentUserDep,
    system_overview: Annotated[str, Form()],
    goals_raw: Annotated[str, Form()],
    notes_raw: Annotated[str | None, Form()] = None,
    environment: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile], File()] = [],
) -> ProjectRead:
    """初期ヒアリング入力(+添付ファイル最大3件、txt/md/pdfのみ)を受け取り、新規プロジェクトを作成する。"""
    intake: dict = {
        "system_overview": system_overview,
        "goals_raw": goals_raw,
        "notes_raw": notes_raw,
        "environment": _parse_environment(environment),
    }
    file_inputs = [
        UploadedFileInput(filename=f.filename or "unnamed", data=await f.read()) for f in files
    ]
    project = await ProjectService(session).create(
        user_id=current_user.id, intake=intake, files=file_inputs
    )
    try:
        await ChatService(session).generate_opening_reply(project)
    except (LLMQuotaExceededError, GenerationFailedError):
        # AIの最初の発話生成に失敗しても、プロジェクト作成自体は成功させる
        # (ユーザーは通常通りチャット欄から発話を始められる)
        pass
    return ProjectRead.model_validate(project)


@router.get("", response_model=list[ProjectRead])
async def list_projects(session: SessionDep, current_user: CurrentUserDep) -> list[ProjectRead]:
    """認証ユーザーのプロジェクト一覧を取得する。"""
    projects = await ProjectRepository(session).list_for_user(current_user.id)
    return [ProjectRead.model_validate(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(session: SessionDep, current_project: CurrentProjectDep) -> ProjectDetail:
    """プロジェクトの詳細(初期ヒアリング入力・添付ファイルサマリを含む)を取得する。"""
    intake_files = await IntakeFileRepository(session).list_for_project(current_project.id)
    return ProjectDetail(
        id=current_project.id,
        title=current_project.title,
        status=current_project.status,  # type: ignore[arg-type]
        created_at=current_project.created_at,
        updated_at=current_project.updated_at,
        intake=current_project.intake,
        intake_files=[IntakeFileRead.model_validate(f) for f in intake_files],
    )


@router.post("/{project_id}/chat")
async def send_hearing_message(
    payload: HearingMessageRequest, session: SessionDep, current_project: CurrentProjectDep
) -> StreamingResponse:
    """ヒアリングチャットへメッセージを送信し、AI応答をSSE(Server-Sent Events)でストリーミング返却する。"""

    async def event_stream():
        async for chunk in ChatService(session).stream_reply(
            current_project, user_message=payload.message
        ):
            yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{project_id}/chat", response_model=list[ChatHistoryRead])
async def get_hearing_history(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[ChatHistoryRead]:
    """プロジェクトのチャット履歴を取得する。"""
    history = await ChatHistoryRepository(session).list_for_project(current_project.id)
    return [ChatHistoryRead.model_validate(entry) for entry in history]


@router.get("/{project_id}/hearing-completion", response_model=HearingCompletionCheck)
async def get_hearing_completion(
    session: SessionDep, current_project: CurrentProjectDep
) -> HearingCompletionCheck:
    """これまでの対話履歴から、ヒアリングが完了条件(5条件)を満たしたかどうかを判定する。
    is_sufficient=Trueでも生成へは自動で進まない(呼び出し側が構造化サマリを提示し、
    ユーザーの明示的な承認を得てから/generateを呼ぶ想定)。"""
    return await ChatService(session).check_completion(current_project)


@router.post("/{project_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def trigger_generation(
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> None:
    """設計書4種の一括生成(+自己診断)をバックグラウンドでトリガーする。

    project_id・user_idの値のみをbackground taskへ渡す(SessionDepのセッションはbackground task
    実行前にクローズされるため、リクエストのセッションやORMオブジェクトはそのまま渡さない。詳細は
    doc_generator_service.generate_documentsのdocstring参照)。user_idも渡すのは、所有者チェック
    無しの専用メソッドを増やすのではなく、既存の所有者スコープ版get_by_idに統一するため。
    """
    background_tasks.add_task(generate_documents, current_project.id, current_user.id)

@router.get("/{project_id}/documents", response_model=list[GeneratedDocumentRead])
async def list_generated_documents(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[GeneratedDocumentRead]:
    """生成された設計書(各doc_typeの最新バージョンのみ)一覧を取得する。"""
    documents = await GeneratedDocumentRepository(session).list_latest_for_project(
        current_project.id
    )
    return [GeneratedDocumentRead.model_validate(d) for d in documents]

@router.get("/{project_id}/documents/{doc_id}/download")
async def download_generated_document(
    doc_id: uuid.UUID, session: SessionDep, current_project: CurrentProjectDep
) -> Response:
    """指定したバージョンの設計書をMarkdownファイルとしてダウンロードする
    (docs/external_design.md 2.5節4項: 本リポジトリ`docs/`配下の実ファイル名
    `requirements.md`/`external_design.md`/`internal_design.md`/`implementation_plan.md`
    に合わせ`{document_type}.md`とする)。"""
    document = await GeneratedDocumentRepository(session).get_by_id(doc_id)
    if document is None or document.project_id != current_project.id:
        raise DocumentNotFoundError(f"Document {doc_id} not found")

    filename = f"{document.doc_type}.md"
    return Response(
        content=document.content,
        media_type="text/markdown",
        headers={"Content-Disposition": _content_disposition(filename)},
    )