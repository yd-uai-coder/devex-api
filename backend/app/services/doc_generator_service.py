import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.gemini import extract_text_content, get_gemini_llm
from app.core.database import AsyncSessionLocal
from app.models.chat_history import ChatHistory
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import DOC_TYPES, GeneratedDocumentRepository
from app.repositories.project import ProjectRepository
from app.services.errors import LLMQuotaExceededError
from app.services.llm_retry import invoke_with_retry

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
_DOC_TYPE_PROMPTS: dict[str, str] = {
    "requirements": (
        "あなたは優秀なITコンサルタント・システムアナリストです。"
        "入力された【プロジェクト概要情報】をもとに、システム開発の「要件定義書」をMarkdown形式で"
        "作成してください。前置きや断り書きは不要です。Markdown本文のみを出力してください。\n\n"
        "出力フォーマット:\n"
        "# 1. 要件定義書\n\n"
        "## 1.1 プロジェクトの目的・背景\n- 解決したい課題、目指すゴール\n\n"
        "## 1.2 ターゲットユーザー・利用シーン\n"
        "- 主なユーザー層と想定される使用シチュエーション\n\n"
        "## 1.3 システム化の範囲（In Scope / Out of Scope）\n"
        "- 今回開発する範囲（In Scope）\n"
        "- 今回は開発しない範囲（Out of Scope）\n\n"
        "## 1.4 機能要件（MoSCoW優先度）\n"
        "- **Must have（必須機能）**: \n- **Should have（推奨機能）**: \n"
        "- **Could have（あると良い機能）**: \n- **Won't have（見送り機能）**: \n\n"
        "## 1.5 非機能要件\n- パフォーマンス・応答性\n"
        "- セキュリティ・権限管理\n- 運用・保守・拡張性\n\n"
        "## 1.6 制約条件・前提条件\n- 技術スタックの制約、予算・スケジュールの前提"
        + _COMMON_FORMAT_GUIDANCE
    ),
    "external_design": (
        "あなたは優秀なUI/UXデザイナー・システムアーキテクトです。"
        "以下の【要件定義書】に基づき、ユーザー視点・外部仕様に関する「外部設計書」をMarkdown形式で"
        "作成してください。前置きや断り書きは不要です。Markdown本文のみを出力してください。\n\n"
        "出力フォーマット:\n"
        "# 2. 外部設計書\n\n"
        "## 2.1 システム構成概要\n- システムの全体像と外部との関係性（テキストによる構造記述）\n\n"
        "## 2.2 画面一覧・画面遷移フロー（概要）\n"
        "- 画面一覧は表形式(画面ID/画面名/主要な役割/優先度)で示す\n"
        "- 画面遷移はテキスト図(コードブロック)で示す\n\n"
        "## 2.3 主要画面のUI/UX仕様\n"
        "- 主要画面ごとに`### 画面ID: 画面名`の小見出しを立て、構成要素"
        "（入力項目、表示項目、操作ボタン、アクション）を記述する\n\n"
        "## 2.4 外部システム・API連携仕様\n- 連携する外部サービス／API、"
        "認証方式、データ連携タイミング\n\n"
        "## 2.5 データ入出力仕様\n- ファイル入出力（CSV、JSON等）、"
        "受付フォーマット・バリデーションルール"
        + _COMMON_FORMAT_GUIDANCE
    ),
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
        "- テーブルごとに見出しを立て、"
        "Markdownテーブル(カラム名/データ型/制約/説明)で全カラムを列挙する\n\n"
        "## 3.3 バックエンド処理・モジュール設計\n"
        "- 主要処理ロジック（ビジネスロジック）の分割方針。ディレクトリ構成はコードブロックで示す\n"
        "- APIエンドポイント一覧はMarkdownテーブル(メソッド/パス/概要)で示す\n\n"
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
        "- フェーズ1（MVP / 必須機能）、フェーズ2（拡張機能）などの段階的リリース計画\n\n"
        "## 4.2 タスク分解（WBS案）\n"
        "- タスクは`- [ ] タスク内容`のチェックボックス形式で列挙する\n"
        "- 要件確認・環境構築 / バックエンド開発 / フロントエンド開発 / "
        "統合テスト / デプロイのタスクリスト\n\n"
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
# ここに無いdoc_type(requirements)は、代わりにチャット全履歴(transcript)を直接読む。
# 後続の文書は生のチャット履歴を再解釈せず、前段の確定済み文書だけを入力にすることで、
# 4文書間の一貫性を確保する。
_DOC_TYPE_INPUTS: dict[str, tuple[str, ...]] = {
    "external_design": ("requirements",),
    "internal_design": ("requirements", "external_design"),
    "implementation_plan": ("requirements", "internal_design"),
}

_SELF_DIAGNOSIS_SYSTEM_PROMPT = (
    "あなたはレビュアーです。以下は今しがた生成された4種の設計書です。"
    "各ドキュメントの不足・不明瞭な点を「最重要(実装着手に支障)」「中程度(後続の設計判断に影響)」"
    "「軽微(運用上の補足)」の3段階に分類し、日本語の箇条書きで指摘してください。"
    "指摘が無い場合は、その旨を簡潔に述べてください。"
)

async def generate_documents(project_id: uuid.UUID, user_id: uuid.UUID, *, llm=None) -> None:
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
        await DocGeneratorService(session).generate(project_id, user_id, llm=llm)


class DocGeneratorService:
    """ヒアリング完了後の4種ドキュメント一括生成+自己診断を担当するサービス。"""

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション(background task用に独立して開始されたもの)
        self._session = session
        self._projects = ProjectRepository(session)
        self._chat_histories = ChatHistoryRepository(session)
        self._documents = GeneratedDocumentRepository(session)


    async def generate(self, project_id: uuid.UUID, user_id: uuid.UUID, *, llm=None) -> None:
        """チャット全履歴をコンテキストに4種の設計書を生成し、続けて自己診断を行う。
        プロジェクトが見つからない場合は何もしない(background task内なので例外を送出しても
        呼び出し元には伝播しないため、静かに終了する)。

        LLM呼び出し失敗時(quota超過等)は`status`を呼び出し前の状態(`interviewing`または
        `revising`)に戻し再試行可能にする ── 失敗時に`generating`のまま固定されると
        ユーザーが再度「生成する」を押せなくなるため。`revising`から始まった再生成が
        失敗した場合に`interviewing`へ巻き戻すと、既に生成済みだったという文脈が失われる
        ため、開始時点の状態を記憶しておいて戻す。
        失敗内容は`sender='others'`のchat_historiesにも記録し、チャット画面に表示できるようにする。
        """
        project = await self._projects.get_by_id(project_id, user_id=user_id)
        if project is None:
            return

        status_before_generation = project.status
        project.status = "generating"
        await self._session.commit()

        try:
            llm = llm or get_gemini_llm()
            history = await self._chat_histories.list_for_project(project_id)
            transcript = _render_transcript(history)

            generated: dict[str, str] = {}
            for doc_type in DOC_TYPES:
                content = await self._generate_one(doc_type, transcript, generated, llm=llm)
                await self._documents.create_version(
                    project_id=project_id, doc_type=doc_type, content=content
                )
                generated[doc_type] = content

            diagnosis = await self._self_diagnose(generated, llm=llm)
            await self._chat_histories.add(
                project_id=project_id, sender="others", message=diagnosis
            )


        except LLMQuotaExceededError:
            # LLM_QUOTA_EXCEEDEDはdocs/internal_design.md 3.4節が定めるとおり、
            # ユーザーに分かりやすい専用メッセージを表示する。
            project.status = status_before_generation
            await self._chat_histories.add(
                project_id=project_id,
                sender="others",
                message="本日の利用上限に達しました。時間をおいて再度お試しください。",
            )
            await self._session.commit()
            return
        except Exception as exc:  # noqa: BLE001 -- それ以外の失敗要因も再試行可能な状態に戻す
            project.status = status_before_generation
            await self._chat_histories.add(
                project_id=project_id,
                sender="others",
                message=f"設計書の生成に失敗しました。時間をおいて再度お試しください。({exc})",
            )
            await self._session.commit()
            return

        project.status = "completed"
        await self._session.commit()

    async def _generate_one(
        self, doc_type: str, transcript: str, generated: dict[str, str], *, llm
    ) -> str:
        """1種類のドキュメントを、doc_type専用のプロンプトでMarkdownとして生成する。
        requirementsのみチャット全履歴(transcript)を直接読み、それ以外は前段で確定済みの
        文書(generated、_DOC_TYPE_INPUTSが参照関係を定義)を入力にする。
        単発呼び出しのためinvoke_with_retryでラップする。"""
        system_prompt = _DOC_TYPE_PROMPTS[doc_type]
        if doc_type in _DOC_TYPE_INPUTS:
            human_content = "\n\n".join(
                f"## {_DOC_TYPE_LABELS[dep]}\n{generated[dep]}"
                for dep in _DOC_TYPE_INPUTS[doc_type]
            )
        else:
            human_content = transcript
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_content),
        ]

        async def _call() -> str:
            response = await llm.ainvoke(messages)
            return extract_text_content(response.content)

        return await invoke_with_retry(_call)


    async def _self_diagnose(self, generated: dict[str, str], *, llm) -> str:
        """生成済みの4文書をまとめて入力し、不足・不明瞭な点を3段階で自己診断させる。"""
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

        return await invoke_with_retry(_call)


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
