import uuid
from datetime import UTC, datetime

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.gemini import extract_text_content, get_gemini_llm
from app.core.database import AsyncSessionLocal
from app.documents.external_design import (
    ExternalDesignModel,
    render_external_design,
    row_counts,
    validate_external_design,
)
from app.documents.external_design import build_model as build_external_design_model
from app.documents.external_design.drafting import (
    ExternalDesignGenerationOutput,
    build_external_design_messages,
)
from app.documents.external_design.drafting import to_draft as to_external_design_draft
from app.documents.issues import DocumentIssue
from app.documents.requirements import (
    RequirementsModel,
    build_model,
    mark_adopted,
    render_requirements,
    source_counts,
    validate_requirements,
)
from app.documents.requirements.drafting import (
    RequirementsGenerationOutput,
    build_requirements_messages,
    to_draft,
)
from app.hearing import as_scale
from app.models.chat_history import ChatHistory
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import DOC_TYPES, GeneratedDocumentRepository
from app.repositories.hearing_point import HearingPointRepository
from app.repositories.project import ProjectRepository
from app.schemas.document import (
    AssumedRequirementRead,
    DocumentIssueRead,
    ExternalDesignSummaryRead,
    GeneratedDocumentRead,
    RequirementsSummaryRead,
)
from app.services.errors import (
    DocGenerationInProgressError,
    DocumentNotFoundError,
    LLMQuotaExceededError,
    RequirementNotAssumedError,
    RequirementNotFoundError,
)
from app.services.generation_staleness import is_stale
from app.services.llm_retry import invoke_structured, invoke_with_retry

logger = structlog.get_logger(__name__)

_DOC_TYPE_LABELS: dict[str, str] = {
    "requirements": "要件定義書",
    "external_design": "外部設計書",
    "internal_design": "内部設計書",
    "implementation_plan": "実装計画書",
}

# 4文書に共通する出力上の心がけ。当プロジェクト自身の4文書(docs/requirements.md等)を
# 実際に作成した際、個別プロンプトの指示を超えて一貫して採用されていた書式(表形式の多用・
# 複数観点の番号付きサブセクション化・構造図のコードブロック化)を、ドメインに依存しない
# 一般的な指針として全doc_typeへ横展開したもの。
_COMMON_FORMAT_GUIDANCE = (
    "\n\n出力全体の心がけ:\n"
    "- 一覧性の高い情報(画面一覧・テーブル定義・APIエンドポイント一覧等)は"
    "Markdownテーブルで整理する。\n"
    "- 1つの見出しが複数の観点を含む場合は、意味のある小見出し付きの番号付きサブセクション"
    "(### 1. ... / ### 2. ...)に展開する。\n"
    "- システム構成・ディレクトリ構成・画面遷移フロー等の構造は、"
    "コードブロック内のテキスト図で視覚的に示す。"
)

# doc_typeごとに特化したシステムプロンプト(docs/internal_design.md 3.3節)。
# 出力フォーマット(見出し構成)を明示することで、4文書の粒度・構成を安定させる。
# 要件定義書・外部設計書はここに無い(構造化出力 → model → md。app/documents/requirements・
# app/documents/external_design の drafting.py)。外部設計書の見出し(2.1〜2.6)と 2.6 の列は
# app/documents/external_design/markdown.py が組み立てる。
_DOC_TYPE_PROMPTS: dict[str, str] = {
    "internal_design": (
        "あなたは優秀なリードエンジニア・データベース設計者です。"
        "以下の【要件定義書】および【外部設計書】に基づき、システム内部の構造・ロジックに関する"
        "「内部設計書」をMarkdown形式で作成してください。前置きや断り書きは不要です。"
        "Markdown本文のみを出力してください。\n\n"
        "出力フォーマット:\n"
        "# 3. 内部設計書\n\n"
        "## 3.1 技術スタック選定・アーキテクチャ方針\n"
        "- フロントエンド / バックエンド / データベース / インフラの選定理由と構成方針\n\n"
        "## 3.2 データモデル定義\n- 主要エンティティ一覧\n"
        "- テーブルごとに『### テーブル: <テーブル名>』の見出しを立て、"
        "Markdownテーブル(カラム名/データ型/制約/説明)で全カラムを列挙する\n\n"
        "## 3.3 バックエンド処理・モジュール設計\n"
        "- 主要処理ロジック（ビジネスロジック）の分割方針。ディレクトリ構成はコードブロックで示す\n"
        "- APIエンドポイント一覧はMarkdownテーブル(メソッド/パス/概要)で示す。"
        "外部設計書2.6のAPI一覧と同じメソッド・パスを使い、内部の担当(ルート・サービス)の"
        "観点で概要を書く\n"
        "- 続けて『### モジュール一覧』の小節を設け、ファイル単位の責務を"
        "Markdownテーブル(パス/層/責務/主な依存先)で示す。"
        "スキーマ・設定のような定型のファイルは省くか1行にまとめる\n"
        "- 続けて『### 処理別データフロー』の小節を設け、APIエンドポイント・バッチ処理ごとに"
        "『#### DF-<連番>: <HTTPメソッド> <パス>』(バッチは『#### DF-<連番>: <バッチ名>』)の"
        "見出しを立てる\n"
        "  - 各見出しの下に、データの流れをMarkdownテーブル(元/データ/変換/先)で示す。"
        "元・先は外部実体(利用者・外部サービス)、処理(担当モジュール名)、"
        "データストア(テーブル名)のいずれかとする\n"
        "  - 続けて、その処理に登場するデータ項目を"
        "『- データ項目: <名前>(<フィールド1>, <フィールド2>, ...)』の形式で列挙する。"
        "同じデータ項目は全処理で同じ名前を使う\n\n"
        "## 3.4 例外処理・エラーハンドリング・ログ設計\n"
        "- 共通エラーレスポンス形式、例外検知・ログ出力方針"
        + _COMMON_FORMAT_GUIDANCE
    ),
    "implementation_plan": (
        "あなたは経験豊富なプロジェクトマネージャーです。"
        "以下の設計情報（要件定義・内部設計）を踏まえ、開発を安全かつ確実に進めるための「実装計画書」を"
        "Markdown形式で作成してください。前置きや断り書きは不要です。Markdown本文のみを出力してください。\n\n"
        "出力フォーマット:\n"
        "# 4. 実装計画書\n\n"
        "## 4.1 開発フェーズ分割・マイルストーン\n"
        "- 各フェーズの見出しに『── 【優先度】』の形式でMoSCoW優先度を明記する\n"
        "- フェーズ1（MVP / 必須機能）、フェーズ2（拡張機能）などの段階的リリース計画\n"
        "- 各フェーズに含めるマイルストーンを、4.2のID(M-01など)で挙げる\n\n"
        "## 4.2 タスク分解（WBS）\n"
        "- タスクは実装手順書の作業単位になる。機能ごとの縦割りにし、1つのタスクに"
        "その処理のバックエンド・フロントエンド・テストを含める。処理の無い準備・デプロイは"
        "基盤のタスクにする\n"
        "- マイルストーンごとに『### M-<2桁の連番>: <マイルストーン名> ── 【Must/Should/Could】』"
        "の見出しを立て、直後に『- ゴール: <動くようになること>』を書く。"
        "この節にはマイルストーン以外の見出しを立てない\n"
        "- タスクは『- [ ] M-<2桁>-T<2桁> [機能] <タスク名>』(処理の無いものは[基盤])の形式で書き、"
        "マイルストーンの中で01から連番を振る\n"
        "- 各タスクの下に、字下げした箇条書きで次の欄を書く(該当が無い欄は『なし』):\n"
        "  - 『処理: DF-<連番>, ...』内部設計書の処理別データフローのID。"
        "機能のタスクは原則1つ(多くても3つ)、基盤のタスクは『なし』\n"
        "  - 『要件: FR-<3桁>, ...』そのタスクが実現する、要件定義書の機能要件のID。"
        "要件定義書に無いIDは作らない。基盤のタスクは『なし』\n"
        "  - 『依存: M-<2桁>-T<2桁>, ...』先に終わっている必要があるタスクのID。"
        "前に書いたタスクだけを指す\n"
        "  - 『モジュール: <パス>, ...』作る・直すファイル。"
        "内部設計書のモジュール一覧のパスを使う\n"
        "  - 『環境・設定: <ファイル>, ...』Dockerfile・docker-compose.ymlのような"
        "環境・設定のファイル\n"
        "- 内部設計書の処理別データフローは、すべていずれかの機能のタスクに入れる。"
        "要件定義書の Must の機能要件は、すべていずれかのタスクの『要件』に入れる。"
        "Won't haveの機能はタスクにしない\n"
        "- 例:\n"
        "```\n"
        "### M-01: 予約の登録 ── 【Must】\n"
        "- ゴール: 備品を予約して一覧で確かめられる\n"
        "- [ ] M-01-T01 [基盤] 開発環境とDBを用意する\n"
        "  - 処理: なし\n"
        "  - 要件: なし\n"
        "  - 依存: なし\n"
        "  - モジュール: backend/app/main.py\n"
        "  - 環境・設定: docker-compose.yml\n"
        "- [ ] M-01-T02 [機能] 予約を登録する\n"
        "  - 処理: DF-1\n"
        "  - 要件: FR-001\n"
        "  - 依存: M-01-T01\n"
        "  - モジュール: backend/app/api/reservations.py, backend/app/services/reservation.py\n"
        "  - 環境・設定: なし\n"
        "```\n\n"
        "## 4.3 開発環境・CI/CD・事前準備事項\n- 開発に必要なツール、"
        "リポジトリ構成、自動テスト・CI/CD方針\n\n"
        "## 4.4 想定リスクと対策（トレードオフ・後回し候補）\n"
        "- 各リスクは『**リスクN: 内容**』の見出し+『*対策*: ...』の形式で記述する\n"
        "- 想定される技術的・スケジュール的リスク\n"
        "- 納期・予算超過時のトレードオフ策（機能削減・代替手段案）"
        + _COMMON_FORMAT_GUIDANCE
    ),
}

# 各doc_typeの入力として使う、前段で確定済みの文書(doc_type名のタプル)。
# ここに無いdoc_type(requirements)は、代わりにチャット全履歴(transcript)と観点の表を読む。
# 外部設計書(_generate_external_design)は、要件定義書の md に加えて、生成した要件定義書の
# model(要件ID の照合)も受け取る。
# 後続の文書は生のチャット履歴を再解釈せず、前段の確定済み文書だけを入力にすることで、
# 4文書間の一貫性を確保する。
_DOC_TYPE_INPUTS: dict[str, tuple[str, ...]] = {
    "external_design": ("requirements",),
    "internal_design": ("requirements", "external_design"),
    "implementation_plan": ("requirements", "internal_design"),
}

# モード(projects.mode)ごとに生成する文書。詳細設計モードは要件定義・外部設計だけを生成し、
# 内部設計・実装計画に当たる部分は段階(docs/internal_design.md 3.3節「4. 詳細設計モード」)
# へ引き継ぐ。
# 並びは生成の順序で、_DOC_TYPE_INPUTSの前段が必ず先に来る。
DOC_TYPES_BY_MODE: dict[str, tuple[str, ...]] = {
    "simple": DOC_TYPES,
    "detailed": ("requirements", "external_design"),
}

_SELF_DIAGNOSIS_SYSTEM_PROMPT = (
    "あなたはレビュアーです。以下は今しがた生成された設計書です。"
    "各ドキュメントの不足・不明瞭な点を「最重要(実装着手に支障)」「中程度(後続の設計判断に影響)」"
    "「軽微(運用上の補足)」の3段階に分類し、日本語の箇条書きで指摘してください。"
    "指摘が無い場合は、その旨を簡潔に述べてください。"
)

# 生成の失敗・中断をチャットに残す文言(生の例外の文字列は利用者に見せず、ログにだけ残す)
_QUOTA_MESSAGE = "本日の利用上限に達しました。時間をおいて再度お試しください。"
_FAILED_MESSAGE = "設計書の生成に失敗しました。時間をおいて再度お試しください。"
_STALE_MESSAGE = (
    "設計書の生成が時間内に終わらなかったため、中断しました。もう一度生成してください。"
)


async def generate_documents(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    status_before_generation: str | None = None,
    *,
    llm=None,
) -> None:
    """`BackgroundTasks`から呼び出すエントリポイント。

    FastAPI 0.106以降、`yield`を使うDIのセッション(SessionDep)はbackground task実行"前"に
    クローズされるため、background task内でリクエストのセッションをそのまま使うことはできない。
    そのためこの関数はセッションを自前で開始・終了する(公式ドキュメントの推奨パターン:
    "pass identifiers ... retrieve the necessary objects inside the background task itself")。

    project_idに加えuser_idも受け取るのは、所有者チェック無しの専用メソッド(get_by_id_unscoped)を
    リポジトリに増やすのではなく、既存の所有者スコープ版get_by_idへ統一するため。project_id・
    user_idはどちらも値であり、セッションのようにリクエストのライフサイクルに紐づくものではない
    ため、background taskへ渡すこと自体に技術的な制約は無い。
    """
    async with AsyncSessionLocal() as session:
        await DocGeneratorService(session).generate(
            project_id, user_id, status_before_generation=status_before_generation, llm=llm
        )


class DocGeneratorService:
    """ヒアリング完了後の4種ドキュメント一括生成+自己診断を担当するサービス。"""

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション(background task用に独立して開始されたもの)
        self._session = session
        self._projects = ProjectRepository(session)
        self._chat_histories = ChatHistoryRepository(session)
        self._documents = GeneratedDocumentRepository(session)
        self._hearing_points = HearingPointRepository(session)

    async def request_generation(self, project: Project) -> str:
        """生成の要求を受け付け、`status`を`generating`にしてcommitする。戻り値は受け付ける前の状態
        (失敗したときに戻す先。バックグラウンドの`generate`へ渡す)。

        生成中なら`DocGenerationInProgressError`(409)にする。受け付けと同じリクエストの中で
        `generating`にするのは、バックグラウンドで切り替えると、切り替わる前の二度押しを
        すり抜けさせてしまうため。止まった「生成中」は先に回収する(`recover_if_stale`)。"""
        await self.recover_if_stale(project)
        if project.status == "generating":
            raise DocGenerationInProgressError(
                "設計書を生成しています。完了してから再度お試しください。"
            )
        status_before_generation = project.status
        project.status = "generating"
        await self._session.commit()
        # updated_at(生成を始めた時刻。回収の判定に使う)はDB側で決まるため読み直す
        await self._session.refresh(project)
        return status_before_generation

    async def recover_if_stale(self, project: Project, *, now: datetime | None = None) -> bool:
        """しきい値(15分)を超えて`generating`のままのプロジェクトを、生成前の状態へ戻す
        (app/services/generation_staleness.py)。戻した場合はTrue。

        生成前の状態は記録していないため、文書が既にあれば`revising`、無ければ`interviewing`にする
        (どちらも再度「生成する」を押せる状態)。中断したことはチャットに残す。"""
        if project.status != "generating":
            return False
        # updated_at はDB側で更新されるため、失効していれば読み直す(非同期では暗黙に読めない)
        await self._session.refresh(project, ["updated_at"])
        if not is_stale(project.updated_at, now or datetime.now(UTC)):
            return False
        has_documents = bool(await self._documents.list_current_for_project(project.id))
        project.status = "revising" if has_documents else "interviewing"
        await self._chat_histories.add(
            project_id=project.id, sender="others", message=_STALE_MESSAGE
        )
        await self._session.commit()
        await self._session.refresh(project)
        logger.warning("documents_generation_stale", project_id=str(project.id))
        return True

    async def generate(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        status_before_generation: str | None = None,
        llm=None,
    ) -> None:
        """チャット全履歴をコンテキストに、モードごとの設計書(simpleは4種、detailedは要件定義・
        外部設計の2種。DOC_TYPES_BY_MODE)を生成し、続けて自己診断を行う。
        プロジェクトが見つからない場合は何もしない(background task内なので例外を送出しても
        呼び出し元には伝播しないため、静かに終了する)。

        LLM呼び出し失敗時(quota超過等)は`status`を呼び出し前の状態(`interviewing`または
        `revising`)に戻し再試行可能にする ── 失敗時に`generating`のまま固定されると
        ユーザーが再度「生成する」を押せなくなるため。`revising`から始まった再生成が
        失敗した場合に`interviewing`へ巻き戻すと、既に生成済みだったという文脈が失われる
        ため、開始時点の状態を記憶しておいて戻す。
        失敗内容は`sender='others'`のchat_historiesにも記録し、チャット画面に表示できるようにする。
        失敗したときは、途中まで作った版と古い版の削除を rollback で取り消してから状態を戻す
        (4文書の組がそろわない状態を残さないため)。

        `status_before_generation`は`request_generation`が受け付けたときの状態。省略したときは
        (受け付けを経ない呼び出し)ここで`generating`へ切り替える。
        """
        project = await self._projects.get_by_id(project_id, user_id=user_id)
        if project is None:
            return

        if status_before_generation is None:
            status_before_generation = project.status
            project.status = "generating"
            await self._session.commit()

        try:
            llm = llm or get_gemini_llm()
            history = await self._chat_histories.list_for_project(project_id)
            transcript = _render_transcript(history)

            generated: dict[str, str] = {}
            requirements: RequirementsModel | None = None
            doc_types = DOC_TYPES_BY_MODE[project.mode]
            for doc_type in doc_types:
                model: dict | None = None
                if doc_type == "requirements":
                    content, requirements = await self._generate_requirements(
                        project, transcript, llm=llm
                    )
                    model = requirements.model_dump(mode="json")
                elif doc_type == "external_design":
                    content, model = await self._generate_external_design(
                        project, generated["requirements"], requirements, llm=llm
                    )
                else:
                    content = await self._generate_one(doc_type, transcript, generated, llm=llm)
                await self._documents.create_version(
                    project_id=project_id, doc_type=doc_type, content=content, model=model
                )
                generated[doc_type] = content

            diagnosis = await self._self_diagnose(generated, llm=llm)
            await self._chat_histories.add(
                project_id=project_id, sender="others", message=diagnosis
            )


        except Exception as exc:  # noqa: BLE001 -- どの失敗要因も再試行可能な状態に戻す
            await self._session.rollback()
            logger.warning(
                "documents_generation_failed",
                project_id=str(project_id),
                error_type=type(exc).__name__,
            )
            # rollbackで失効したプロジェクトを読み直してから、状態を戻す
            project = await self._projects.get_by_id(project_id, user_id=user_id)
            if project is None:
                return
            project.status = status_before_generation
            # LLM_QUOTA_EXCEEDEDはdocs/internal_design.md 3.4節が定めるとおり、
            # ユーザーに分かりやすい専用メッセージを表示する。
            await self._chat_histories.add(
                project_id=project_id,
                sender="others",
                message=(
                    _QUOTA_MESSAGE if isinstance(exc, LLMQuotaExceededError) else _FAILED_MESSAGE
                ),
            )
            await self._session.commit()
            return

        project.status = "completed"
        await self._session.commit()
        logger.info(
            "documents_generated", project_id=str(project_id), doc_count=len(generated)
        )


    async def list_current_documents(self, project_id: uuid.UUID) -> list[GeneratedDocument]:
        """生成された設計書(各doc_typeの現在表示中のバージョンのみ)一覧を取得する。"""
        return await self._documents.list_current_for_project(project_id)

    async def get_document(self, *, project: Project, doc_id: uuid.UUID) -> GeneratedDocument:
        """指定ドキュメントを1件取得する(他プロジェクトのものは404扱い)。
        GeneratedDocumentRepositoryはProjectRepositoryのような所有権スコープの
        get_by_idを持たないため、その意味づけ(見つからなければNotFound)をここに集約する
        (ルーターがRepositoryを直接参照しないため)。"""
        document = await self._documents.get_by_id(doc_id)
        if document is None or document.project_id != project.id:
            raise DocumentNotFoundError(f"Document {doc_id} not found")
        return document

    async def list_versions(self, project_id: uuid.UUID, doc_type: str) -> list[GeneratedDocument]:
        """指定doc_typeの保管済み全バージョン(最大3件)を新しい順に返す。"""
        return await self._documents.list_versions(project_id=project_id, doc_type=doc_type)

    async def restore_version(
        self, project_id: uuid.UUID, doc_type: str, version: int
    ) -> GeneratedDocument:
        """指定バージョンを表示中(is_current)に切り替える。バージョン番号は増やさず、新しい行も
        作らない(既存版の内容も書き換えない)。指定バージョンが存在しなければDocumentNotFoundError。"""
        restored = await self._documents.set_current(
            project_id=project_id, doc_type=doc_type, version=version
        )
        if restored is None:
            raise DocumentNotFoundError(f"{doc_type} version {version} not found")
        await self._session.commit()
        return restored

    async def adopt_requirement(self, project: Project, req_id: str) -> GeneratedDocument:
        """現在の版の要件定義書の推測の要件を、利用者が確かめた「採用」にする(LLMは呼ばない)。

        同じ版の model と md を書き換え、版は増やさない(中身は変わらないので、後続の段階を
        古くしない)。生成中は409。要件が無い・model の無い版は404、推測でない要件は409。"""
        if project.status == "generating":
            raise DocGenerationInProgressError(
                "設計書を生成しています。完了してから再度お試しください。"
            )
        document = await self._documents.get_current(
            project_id=project.id, doc_type="requirements"
        )
        if document is None:
            raise DocumentNotFoundError("requirements not found")
        model = parse_requirements_model(document.model)
        if model is None or all(row.id != req_id for row in model.rows):
            raise RequirementNotFoundError(f"Requirement {req_id} not found")
        adopted = mark_adopted(model, req_id)
        if adopted is None:
            raise RequirementNotAssumedError(f"Requirement {req_id} is not assumed")
        await self._documents.replace_model(
            document,
            content=render_requirements(adopted),
            model=adopted.model_dump(mode="json"),
        )
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def _generate_requirements(
        self, project: Project, transcript: str, *, llm
    ) -> tuple[str, RequirementsModel]:
        """要件定義書を構造化出力で下書きし、model と md を作る。入力は会話・観点の表・前の版の
        要件(ID を引き継ぐため)。出どころは観点の状態を上限に直す(app/documents/requirements)。"""
        states = await self._hearing_points.load_states(project.id)
        scale = as_scale(project.hearing_scale)
        current = await self._documents.get_current(
            project_id=project.id, doc_type="requirements"
        )
        previous = parse_requirements_model(current.model if current else None)
        messages = build_requirements_messages(transcript, states, scale, previous)
        output = await invoke_structured(llm, RequirementsGenerationOutput, messages)
        model = build_model(to_draft(output), previous, states, scale)
        return render_requirements(model), model

    async def _generate_external_design(
        self,
        project: Project,
        requirements_md: str,
        requirements: RequirementsModel | None,
        *,
        llm,
    ) -> tuple[str, dict]:
        """外部設計書を構造化出力で下書きし、model と md を作る。入力は要件定義書の md と前の版の
        画面(画面ID を引き継ぐため)。要件ID の照合用に要件定義書の model を写す
        (app/documents/external_design)。"""
        current = await self._documents.get_current(
            project_id=project.id, doc_type="external_design"
        )
        previous = parse_external_design_model(current.model if current else None)
        messages = build_external_design_messages(requirements_md, previous)
        output = await invoke_structured(llm, ExternalDesignGenerationOutput, messages)
        model = build_external_design_model(
            to_external_design_draft(output), previous, requirements
        )
        return render_external_design(model), model.model_dump(mode="json")

    async def _generate_one(
        self, doc_type: str, transcript: str, generated: dict[str, str], *, llm
    ) -> str:
        """要件定義書の後の文書を、doc_type専用のプロンプトでMarkdownとして生成する。
        前段で確定済みの文書(generated、_DOC_TYPE_INPUTSが参照関係を定義)を入力にする。
        単発呼び出しのためinvoke_with_retryでラップする。"""
        system_prompt = _DOC_TYPE_PROMPTS[doc_type]
        human_content = "\n\n".join(
            f"## {_DOC_TYPE_LABELS[dep]}\n{generated[dep]}" for dep in _DOC_TYPE_INPUTS[doc_type]
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_content),
        ]

        async def _call() -> str:
            response = await llm.ainvoke(messages)
            return extract_text_content(response.content)

        return await invoke_with_retry(_call, messages=messages)


    async def _self_diagnose(self, generated: dict[str, str], *, llm) -> str:
        """生成済みの文書(モードにより4種または2種)をまとめて入力し、不足・不明瞭な点を
        3段階で自己診断させる。"""
        combined = "\n\n".join(
            f"## {_DOC_TYPE_LABELS[doc_type]}\n{content}" for doc_type, content in generated.items()
        )
        messages = [
            SystemMessage(content=_SELF_DIAGNOSIS_SYSTEM_PROMPT),
            HumanMessage(content=combined),
        ]

        async def _call() -> str:
            response = await llm.ainvoke(messages)
            return extract_text_content(response.content)

        return await invoke_with_retry(_call, messages=messages)


def _render_transcript(history: list[ChatHistory]) -> str:
    """chat_historiesを、ドキュメント生成プロンプトに埋め込むための読みやすいテキストに変換する。
    sender='others'(過去の自己診断結果)は生成の入力には含めない。"""
    _SPEAKER_LABELS = {
        "user": "ユーザー",
        "ai": "AI",
        "intake": "初期入力",
        "attachment": "添付資料",
    }
    lines = [
        f"[{_SPEAKER_LABELS.get(entry.sender, entry.sender)}] {entry.message}"
        for entry in history
        if entry.sender != "others"
    ]
    return "\n".join(lines)


def parse_requirements_model(raw: dict | None) -> RequirementsModel | None:
    """保存した model を読む。無い・壊れていれば None(md のまま使う)。"""
    if raw is None:
        return None
    try:
        return RequirementsModel.model_validate(raw)
    except ValidationError:
        return None


def requirements_summary(document: GeneratedDocument) -> RequirementsSummaryRead | None:
    """プレビューに出す要件定義書の出どころの件数・推測の要件・検証の指摘(LLMは呼ばない)。
    model の無い版・ほかの文書は None。"""
    if document.doc_type != "requirements":
        return None
    model = parse_requirements_model(document.model)
    if model is None:
        return None
    return RequirementsSummaryRead(
        counts={**source_counts(model)},
        assumed=[
            AssumedRequirementRead(id=r.id, kind=r.kind, text=r.text)
            for r in model.rows
            if r.source == "assumed"
        ],
        issues=_issues_read(validate_requirements(model)),
    )


def parse_external_design_model(raw: dict | None) -> ExternalDesignModel | None:
    """保存した外部設計書の model を読む。無い・壊れていれば None(md のまま使う)。"""
    if raw is None:
        return None
    try:
        return ExternalDesignModel.model_validate(raw)
    except ValidationError:
        return None


def external_design_summary(document: GeneratedDocument) -> ExternalDesignSummaryRead | None:
    """プレビューに出す外部設計書の行数と検証の指摘(LLMは呼ばない)。model の無い版・ほかの
    文書は None。"""
    if document.doc_type != "external_design":
        return None
    model = parse_external_design_model(document.model)
    if model is None:
        return None
    return ExternalDesignSummaryRead(
        counts=row_counts(model), issues=_issues_read(validate_external_design(model))
    )


def _issues_read(issues: list[DocumentIssue]) -> list[DocumentIssueRead]:
    return [
        DocumentIssueRead(level=i.level, code=i.code, message=i.message, targets=list(i.targets))
        for i in issues
    ]


def document_read(document: GeneratedDocument) -> GeneratedDocumentRead:
    """文書の応答。要件定義書は出どころの件数・推測の要件・検証の指摘を、外部設計書は行数と
    検証の指摘を付ける。"""
    read = GeneratedDocumentRead.model_validate(document)
    read.requirements = requirements_summary(document)
    read.external_design = external_design_summary(document)
    return read
