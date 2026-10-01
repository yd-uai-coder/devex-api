import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.intake_file import IntakeFileRepository
from app.repositories.project import ProjectRepository
from app.repositories.prompt_template import PromptTemplateRepository
from app.schemas.project import IntakeFileRead, ProjectDetail, ProjectMode
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import (
    FileTooLargeError,
    PromptTemplateNotFoundError,
    TooManyFilesError,
    UnsupportedFileTypeError,
)
from app.services.intake_file_processor import ALLOWED_FILE_TYPES, extract_text

logger = structlog.get_logger(__name__)

# 初期ヒアリングに添付できるファイル数の上限(docs/external_design.md 2.5節5項)
MAX_FILES_PER_PROJECT = 3
# 1ファイルあたりのサイズ上限(バイト)
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024


@dataclass
class UploadedFileInput:
    """アップロードされたファイル1件分の生データ(FastAPIのUploadFileから変換した最小表現)。
    サービス層をFastAPI固有の型に依存させないための境界。"""

    filename: str
    data: bytes


class ProjectService:
    """プロジェクト作成(初期ヒアリング入力+添付ファイルの取り込み)を担当するサービス。"""

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._session = session
        self._projects = ProjectRepository(session)
        self._chat_histories = ChatHistoryRepository(session)
        self._intake_files = IntakeFileRepository(session)
        self._prompt_templates = PromptTemplateRepository(session)

    async def create(
        self, *,
        user_id: uuid.UUID,
        intake: dict,
        files: list[UploadedFileInput],
        template_id: uuid.UUID | None = None,
        mode: ProjectMode = "simple",
    ) -> Project:
        """プロジェクトを作成し、初期ヒアリング入力と添付ファイルの内容をchat_historiesへ記録する。"""
        if len(files) > MAX_FILES_PER_PROJECT:
            raise TooManyFilesError(f"添付ファイルは最大{MAX_FILES_PER_PROJECT}件までです")
        for file in files:
            self._validate_file(file)

        if template_id is not None and await self._prompt_templates.get_by_id(template_id) is None:
            raise PromptTemplateNotFoundError(f"Prompt template {template_id} not found")

        title = (intake.get("system_overview") or "").strip()[:255] or "無題のプロジェクト"
        project = await self._projects.create(
            user_id=user_id, title=title, intake=intake, template_id=template_id, mode=mode
        )
        filenames = [file.filename for file in files]
        await self._chat_histories.add(
            project_id=project.id,
            sender="intake",
            message=_format_intake_summary(intake, filenames),

        )

        for file in files:
            await self._ingest_file(project.id, file)

        await self._session.commit()
        logger.info("project_created", project_id=str(project.id))
        return project

    async def list_for_user(self, user_id: uuid.UUID) -> list[Project]:
        """指定ユーザーのプロジェクト一覧を取得する。"""
        return await self._projects.list_for_user(user_id)

    async def get_detail(self, project: Project) -> ProjectDetail:
        """プロジェクトの詳細(初期ヒアリング入力・添付ファイルサマリを含む)を返す。
        Project単体のカラムに加え、別Repository(IntakeFile)の取得・整形も
        このメソッドに集約する(ルーター層はRepositoryを直接参照しない)。

        画面は生成の完了をこの取得のポーリングで待つため、止まった「生成中」(15分超)はここで
        回収する(DocGeneratorService.recover_if_stale)。"""
        await DocGeneratorService(self._session).recover_if_stale(project)
        intake_files = await self._intake_files.list_for_project(project.id)
        return ProjectDetail(
            id=project.id,
            title=project.title,
            status=project.status,  # type: ignore[arg-type]
            mode=project.mode,  # type: ignore[arg-type]
            created_at=project.created_at,
            updated_at=project.updated_at,
            intake=project.intake,
            intake_files=[IntakeFileRead.model_validate(f) for f in intake_files],
            template_id=project.template_id,
        )

    def _validate_file(self, file: UploadedFileInput) -> None:
        """対応形式・サイズ上限を満たさないファイルがあれば、記録前にまとめて弾く。"""
        file_type = _resolve_file_type(file.filename)
        if file_type not in ALLOWED_FILE_TYPES:
            raise UnsupportedFileTypeError(f"対応していないファイル形式です: {file.filename}")
        if len(file.data) > MAX_FILE_SIZE_BYTES:
            raise FileTooLargeError(f"ファイルサイズが上限を超えています: {file.filename}")

    async def _ingest_file(self, project_id: uuid.UUID, file: UploadedFileInput) -> None:
        """1ファイルをテキスト化し、intake_filesへ記録する。成功時はchat_historiesにも追記する
        (sender='attachment' ── チャット画面には表示しないが、ヒアリング対話・ドキュメント生成
        双方のLLMコンテキストとしては引き続き使う。ファイル名自体は_format_intake_summaryが
        intake要約側に表示する)。テキスト化に失敗しても例外は送出しない(ヒアリング自体を
        ブロックしないため)。"""
        file_type = _resolve_file_type(file.filename)
        extracted_text, error_message = await extract_text(file_type=file_type, data=file.data)
        status = "processed" if extracted_text is not None else "failed"
        await self._intake_files.create(
            project_id=project_id,
            filename=file.filename,
            file_type=file_type,
            size_bytes=len(file.data),
            extracted_text=extracted_text,
            status=status,
            error_message=error_message,
        )
        if extracted_text:
            await self._chat_histories.add(
                project_id=project_id,
                sender="attachment",
                message=f"[添付ファイル: {file.filename}]\n{extracted_text}",
            )


def _resolve_file_type(filename: str) -> str:
    """ファイル名の拡張子から file_type("txt"/"md"/"pdf" 等)を求める。"""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _format_intake_summary(intake: dict, filenames: list[str]) -> str:
    """intake(system_overview/goals_raw/notes_raw/environment)を、チャット画面に表示する
    ためのプレーンテキストに整形する。environmentは表示対象外(ヒアリング対話のLLM
    コンテキストには`chat_service._build_messages`側で別途渡すが、表示テキストには含めない)。
    添付ファイルがあれば、ファイル名だけをここに表示する(抽出結果本文はsender='attachment'
    として別途chat_historiesに記録されるが、チャット画面には表示しない)。"""
    lines = [
        f"システム概要：{intake.get('system_overview') or ''}",
        f"実現したい事：{intake.get('goals_raw') or ''}",
    ]
    notes_raw = intake.get("notes_raw")
    if notes_raw:
        lines.append(f"その他備考：{notes_raw}")
    if filenames:
        lines.append(f"添付ファイル：{'、'.join(filenames)}")
    return "\n".join(lines)
