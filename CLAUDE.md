# CLAUDE.md

このファイルは、このリポジトリで作業する人間・AIエージェント双方に向けた、アーキテクチャの全体像と設計判断の「なぜ」を記録したドキュメントです。テンプレートとして新しいプロジェクトの土台に使う際、まずここを読めば構造の意図が分かるようにしています。

## レイヤー構成

```
routes (app/api/routes) → services (app/services) → repositories (app/repositories) → models (app/models)
                                        ↓
                                  ai (app/ai)  ※ LangChain/LangGraphによるLLM連携
```

- **routes**：リクエスト/レスポンスの変換のみを担当し、ビジネスロジックを持たない。ルートは基本的に「サービスを呼ぶ→スキーマに詰めて返す」だけで、例外処理も原則書かない（後述）。
- **services**：ユースケース単位のロジックとトランザクション境界を持つ。DBの`commit()`はサービス層でのみ行う。
- **repositories**：永続化のみを担当し、`flush()`はしても`commit()`はしない（トランザクションの単位はサービス層が握るため）。
- **models**：SQLAlchemyのORM定義のみ。
- **ai**：LLM/検索ツールのクライアントとLangGraphのワークフローをまとめた独立パッケージ。servicesから呼び出される。

## エラーハンドリングの設計

- `app/core/errors.py`に`AppError`とHTTPステータスコード別の基底例外（`NotFoundError`, `ConflictError`など）を定義し、`app/services/errors.py`に具体的なドメイン例外（`UserAlreadyExistsError`など）をまとめている。
- **なぜ`services/errors.py`という1つのleafモジュールに例外を集約しているか**：各サービスファイルに例外を分散させると、あるサービスが別サービスの例外をimportする際に循環importが発生しやすい。依存関係を持たない末端モジュールに全ドメイン例外を集めることで、この問題を構造的に避けている。
- ルートは`AppError`を`try/except`で捕まえず、そのまま伝播させる。`app/api/error_handlers.py`の`register_error_handlers`が一括で`AppError`→JSONレスポンスに変換するため、ルートごとの`try/except HTTPException`のボイラープレートが不要になる。
- **例外：`app/api/deps.py`の認証境界だけは生の`HTTPException`を使う**。認証失敗の理由（トークン不正／期限切れ／ユーザー無効化など）を外部に細かく漏らさないため、あえて`AppError`の仕組みを使わずこの1箇所に閉じている。これは意図的な設計であり、統一漏れではない。

## リポジトリ層

- `app/repositories/base.py`の`CRUDRepository[ModelType]`が、`get_by_id` / `find_one` / `list_all` / `get_or_create` / `count` / `delete`という共通CRUD操作を提供する（PEP 695のジェネリクス構文を使用）。
- 各リポジトリは`model`属性にORMモデルを指定して継承し、Eagerロードが必要な場合は`_default_options()`を上書きする（例：`ConversationRepository`は`Message`を常に`selectinload`する）。
- 新しいエンティティを追加する際は、まずこの基底クラスを継承し、固有のクエリだけを追加メソッドとして書くのが基本パターン。

## レート制限

- `app/services/rate_limit.py`の`RateLimiter`は、Redisのカウンタを用いた汎用的な複数ウィンドウ（時間単位・日単位など）のレート制限クラス。`INCR`+`EXPIRE(NX)`を`pipeline`(MULTI/EXEC)で1往復にまとめている。**なぜ1往復にまとめるか**：別々に送ると`INCR`直後にプロセスが落ちた場合にTTL無しのキーが残り、その利用者が永久に制限される（`EXPIRE`のNXは「TTL未設定のときだけ設定」。Redis 7.0以降が必要）。
- **なぜ`ChatService`のコンストラクタに直接ロジックを書かず、独立したサービスに切り出したか**：レート制限の対象・ウィンドウ・上限値は機能ごとに異なりうるため、`RateLimiter`を汎用部品として切り出し、各サービスは「どのresource名で、どのウィンドウ設定を使うか」だけを指定する形にしている。
- 認証前（login/register）は利用者idが無いため、`app/services/auth_rate_limit.py`の`AuthRateLimiter`がIP（`app/api/deps.py`の`ClientIpDep`）・メール単位で`RateLimiter`を組み合わせて総当たり・大量登録を防ぐ。

## リクエスト保護

- `app/api/middleware.py`の`BodySizeLimitMiddleware`は、`Settings.MAX_REQUEST_BODY_BYTES`を超えるリクエストボディを413で拒否するASGIミドルウェア。**なぜアプリ側にも上限を置くか**：nginxの`client_max_body_size`は、アプリを直接公開した開発環境やnginxを通らない経路（docker内部ネットワーク）では効かないため。
- `app/core/config.py`の`Settings`は、`ENVIRONMENT=production`起動時に危険な設定（弱い`JWT_SECRET_KEY`・`DEBUG=true`）を`model_validator`で拒否する。**なぜ実行時でなく起動時に落とすか**：設定ミスは「動くが危険」な状態になりやすく、気づかれないまま本番運用され続けるリスクが高いため。

## LLM/LangGraph連携

- `app/ai/llm/gemini.py`・`app/ai/tools/tavily.py`は、LLM/検索ツールのクライアントを`lru_cache`でプロセス内キャッシュしている。**理由**：クライアント生成コストを避けつつ、単一プロセス・単一APIキー構成のテンプレートとしてはこれで十分なため。マルチテナントで複数APIキーを使い分ける場合はこのキャッシュ戦略を見直す必要がある。
- `app/ai/graph/`にLangGraphの`StateGraph`ワークフローを定義。ノード（`nodes.py`）・状態（`state.py`）・組み立て（`workflow.py`）を分離している。
- `app/services/chat.py`の`_invoke_with_retry`は、LLM呼び出しをラップし「クォータ超過（429相当）は即座に諦める」「それ以外の一時的エラーは規定回数リトライする」を切り分けている。クォータ超過をリトライしても無駄にAPIコールを消費するだけなので、ここは明確に区別する設計にしている。
- `generate_final_answer`ノードは`with_structured_output`でPydanticスキーマ（`app/schemas/generation.py`の`FinalAnswer`）を使った構造化出力を行う。生文字列のパースに頼らず、LLM出力の型を保証するための最小限のデモ実装。

## テストの分離

- `backend/tests/conftest.py`は、`app.core.database`などをimportする**前**に`os.environ.setdefault(...)`でテスト用の`DATABASE_URL`等を設定している。**なぜこの順序が重要か**：Pythonのimportは一度実行されると以後はキャッシュされるため、先に本物の設定を使うモジュールがimportされてしまうと、後から環境変数を上書きしても手遅れになる。この順序を誤ると、テストが誤って開発/本番用のDBに接続してしまう事故につながる。
- ユニットテストはインメモリSQLite（`db_session`フィクスチャ）、統合テスト（`tests/integration/`）は実際のPostgreSQL/Redis（`docker compose up postgres redis`で起動）を使う。統合テストは`@pytest.mark.integration`でマークされ、デフォルトでは実行されない（`pyproject.toml`の`addopts = "-m 'not integration'"`）。

## 開発用ツール

- `backend/scripts/seed.py`（`uv run python -m scripts.seed`）は、ログインを試すための固定テストユーザーを1人だけ冪等に作成する。**dev専用**（本番DBでは実行しない）。
- `backend/pyproject.toml`の`[tool.pyright]`は、エディタ（VSCode/Pylance）とCLI（`uvx pyright`）の解析ルートを`backend/`に固定する。ワークスペースをどのフォルダで開いても`app`パッケージがfirst-partyとして解決される。`ignore`には現時点の既知の型債務（`app/ai/graph/nodes.py`のLangGraph `GraphState`部分構築など）を列挙している。

## Docker構成

- `backend/Dockerfile`は`base → builder → runtime`の3段構成。開発時は`builder`ステージ（`--reload`付き）をソースのbindマウントと組み合わせて使い、本番は`runtime`ステージ（非rootユーザー、`--reload`無し）を使う。
- `nginx/nginx.conf`（開発用、平文HTTP）と`nginx/nginx.prod.conf`（本番用、HTTP→HTTPSリダイレクト＋certbot対応＋TLS終端）を分離している。
