# CLAUDE.md

このファイルは、このリポジトリ([Devex](https://github.com/yd-uai-coder/devex-project#readme) のバックエンド)で作業する人間・AIエージェント双方に向けた、アーキテクチャの全体像と設計判断の「なぜ」を記録したドキュメントです。機能一覧・起動手順は`README.md`、デプロイ・運用は`OPERATIONS.md`にあります。自作の FastAPI のテンプレートから作り始めたため、層の構成・エラー処理・レート制限・テストの分離はテンプレートに由来します(テンプレートの汎用チャット・LangGraph のデモは削除済み)。

## レイヤー構成

```
routes (app/api/routes) → services (app/services) → repositories (app/repositories) → models (app/models)
                                 │
                                 ├→ ドメイン(純粋関数): app/hearing・documents・detailed_design・uml・review
                                 └→ ai (app/ai/llm)  ※ LangChainによるLLMクライアント
```

- **routes**：リクエスト/レスポンスの変換のみを担当し、ビジネスロジックを持たない。ルートは基本的に「サービスを呼ぶ→スキーマに詰めて返す」だけで、例外処理も原則書かない（後述）。
- **services**：ユースケース単位のロジックとトランザクション境界を持つ。DBの`commit()`はサービス層でのみ行う。
- **repositories**：永続化のみを担当し、`flush()`はしても`commit()`はしない（トランザクションの単位はサービス層が握るため）。
- **models**：SQLAlchemyのORM定義のみ。
- **ai**：LLMクライアントの組み立て(`app/ai/llm/`)。servicesから呼び出される。
- **ドメインのパッケージ**(`app/hearing`・`app/documents`・`app/detailed_design`・`app/uml`・`app/review`)：構造化出力のスキーマ、意味モデル、検証の規則、Markdown の組み立てなど、DB にも LLM にも触らない純粋関数。サービスがここを呼び、結果を保存する。**なぜ分けるか**：生成の規則・検証・文書の組み立てを、DB や LLM のスタブ無しで素のテストにできるため(テストの大半はここを直接呼ぶ)。

## エラーハンドリングの設計

- `app/core/errors.py`に`AppError`とHTTPステータスコード別の基底例外（`NotFoundError`, `ConflictError`など）を定義し、`app/services/errors.py`に具体的なドメイン例外（`UserAlreadyExistsError`など）をまとめている。
- **なぜ`services/errors.py`という1つのleafモジュールに例外を集約しているか**：各サービスファイルに例外を分散させると、あるサービスが別サービスの例外をimportする際に循環importが発生しやすい。依存関係を持たない末端モジュールに全ドメイン例外を集めることで、この問題を構造的に避けている。
- ルートは`AppError`を`try/except`で捕まえず、そのまま伝播させる。`app/api/error_handlers.py`の`register_error_handlers`が一括で`AppError`→JSONレスポンスに変換するため、ルートごとの`try/except HTTPException`のボイラープレートが不要になる。
- **例外：`app/api/deps.py`の認証境界だけは生の`HTTPException`を使う**。認証失敗の理由（トークン不正／期限切れ／ユーザー無効化など）を外部に細かく漏らさないため、あえて`AppError`の仕組みを使わずこの1箇所に閉じている。これは意図的な設計であり、統一漏れではない。

## リポジトリ層

- `app/repositories/base.py`の`CRUDRepository[ModelType]`が、`get_by_id` / `find_one` / `list_all` / `get_or_create` / `count` / `delete`という共通CRUD操作を提供する（PEP 695のジェネリクス構文を使用）。
- 各リポジトリは`model`属性にORMモデルを指定して継承し、Eagerロードが必要な場合は`_default_options()`を上書きする（例：`ReviewRunRepository`は`ReviewRun.findings`を常に`selectinload`する）。
- 新しいエンティティを追加する際は、まずこの基底クラスを継承し、固有のクエリだけを追加メソッドとして書くのが基本パターン。

## レート制限

- `app/services/rate_limit.py`の`RateLimiter`は、Redisのカウンタを用いた汎用的な複数ウィンドウ（時間単位・日単位など）のレート制限クラス。`INCR`+`EXPIRE(NX)`を`pipeline`(MULTI/EXEC)で1往復にまとめている。**なぜ1往復にまとめるか**：別々に送ると`INCR`直後にプロセスが落ちた場合にTTL無しのキーが残り、その利用者が永久に制限される（`EXPIRE`のNXは「TTL未設定のときだけ設定」。Redis 7.0以降が必要）。
- **なぜ`ChatService`のコンストラクタに直接ロジックを書かず、独立したサービスに切り出したか**：レート制限の対象・ウィンドウ・上限値は機能ごとに異なりうるため、`RateLimiter`を汎用部品として切り出し、各サービスは「どのresource名で、どのウィンドウ設定を使うか」だけを指定する形にしている。
- 認証前（login/register/Google）は利用者idが無いため、`app/services/auth_rate_limit.py`の`AuthRateLimiter`がIP（`app/api/deps.py`の`ClientIpDep`）・メール単位で`RateLimiter`を組み合わせて総当たり・大量登録を防ぐ。Googleでのログインと連携の確かめ（マイページからの連携も）は`google_ip`で数え、連携の確かめのパスワード（マイページでは今のパスワード）はログインと同じカウンタでも数える。
- **開発用の上限**：`docker-compose.yml`のbackendの`environment`で、4つの認証の上限を1000回/時に上げている（統合テスト・E2E・手元の確かめで登録とログインを繰り返すため）。`docker-compose.test.yml`・`docker-compose.e2e.yml`はこのファイルに重ねるので同じ値になる。本番は`docker-compose.prod.yml`だけで起動するので、この値は漏れない（本番は`app/core/config.py`の既定値か`.env`の値）。

## リクエスト保護

- `app/api/middleware.py`の`BodySizeLimitMiddleware`は、`Settings.MAX_REQUEST_BODY_BYTES`を超えるリクエストボディを413で拒否するASGIミドルウェア。**なぜアプリ側にも上限を置くか**：nginxの`client_max_body_size`は、アプリを直接公開した開発環境やnginxを通らない経路（docker内部ネットワーク）では効かないため。
- `app/core/config.py`の`Settings`は、`ENVIRONMENT=production`起動時に危険な設定（弱い`JWT_SECRET_KEY`・`DEBUG=true`）を`model_validator`で拒否する。**なぜ実行時でなく起動時に落とすか**：設定ミスは「動くが危険」な状態になりやすく、気づかれないまま本番運用され続けるリスクが高いため。

## LLM連携

- `app/ai/llm/catalog.py`が、AIの提供元(`Provider`: Gemini / Claude)・用途(`Purpose`: チャット・生成・レビュー・判定)・使えるモデルと、モデルごとのタイムアウト・用途ごとの出力の上限を持つ。
- `app/services/llm_resolver.py`の`LlmResolver`が「ユーザーと用途 → (provider, モデル, キー)」を決める。モデルはユーザーの選択(無ければ既定値)、キーは自分のキー(`user_api_keys`、Fernetで暗号化。`app/core/crypto.py`)、無ければ環境変数の既定のキー。**解決はLLMを呼ぶ各サービスが自分のセッションで行う**。PDFの取り込みは、PDFをそのまま渡せるGeminiで固定する。
- `app/ai/llm/factory.py`の`build_llm`がクライアントを作る。実クライアントは`lru_cache`で (provider, モデル, キー, 用途, 温度) ごとに1つ。`timeout`を必ず付け、SDKの中の再試行は止めて`app/services/llm_retry.py`の`invoke_with_retry`に任せる(二重に重ねると1回の呼び出しがタイムアウトを大きく超えるため)。
- `invoke_with_retry`は「クォータ超過(429相当)は即座に諦める」「混雑(503・529など)は間を伸ばして再試行」「それ以外の一時的エラーは規定回数リトライ」を切り分ける。クォータ超過をリトライしても無駄にAPIコールを消費するだけなので、明確に区別する。
- 設計レビューは生成とは別のモデル(既定はClaude Sonnet、直ったかの判定はHaiku)で流す(`app/review/`、`app/services/review_service.py`)。使えるのは superuser か、レビューの用途の自分のキーを持つ利用者だけ(`app/review/policy.py`)。既定のキーで流す回は、ユーザーごとの1日の上限で数える。
- 構造化出力は`with_structured_output`でPydanticスキーマを使い、生文字列のパースに頼らない。両 provider で`json_schema`の方式に揃える。
- 登録されるAPIキーは、費用のかからない呼び出し(モデルの一覧)で確かめてから保存する(`app/ai/llm/key_check.py`。キーはURLに載せずヘッダーで渡す)。
- `E2E_FAKE_LLM=true`のときは決定論的な偽のLLM(`app/ai/llm/fake.py`)を返す。`ENVIRONMENT=production`ではこのフラグ自体を起動時に拒否する。

## テストの分離

- `backend/tests/conftest.py`は、`app.core.database`などをimportする**前**に`os.environ.setdefault(...)`でテスト用の`DATABASE_URL`等を設定している。**なぜこの順序が重要か**：Pythonのimportは一度実行されると以後はキャッシュされるため、先に本物の設定を使うモジュールがimportされてしまうと、後から環境変数を上書きしても手遅れになる。この順序を誤ると、テストが誤って開発/本番用のDBに接続してしまう事故につながる。
- ユニットテストはインメモリSQLite（`db_session`フィクスチャ）、統合テスト（`tests/integration/`）は実際のPostgreSQL/Redis（`docker compose up postgres redis`で起動）を使う。統合テストは`@pytest.mark.integration`でマークされ、デフォルトでは実行されない（`pyproject.toml`の`addopts = "-m 'not integration'"`）。
- **統合テスト専用のPostgres DB(`$POSTGRES_TEST_DB`)を開発用DB(`$POSTGRES_DB`)とは別に用意している**（`docker-compose.test.yml`、`postgres-init/01-create-test-db.sh`）。`tests/integration/conftest.py`の`client`フィクスチャはテストのたびに`Base.metadata.create_all`/`drop_all`でテーブルを作り直すため、開発用DBと同じDBに向けて実行すると開発中のデータ・スキーマを消してしまう（実際にこの事故が発生し、`alembic upgrade head`だけでは復旧できなかった ── `Base.metadata.drop_all`はAlembicの管理外の削除のため、`alembic_version`テーブルは「最新適用済み」のまま実テーブルだけ消えるという食い違いが起きる。復旧には`alembic_version`テーブル自体を削除してから`alembic upgrade head`をやり直す必要がある）。統合テストは必ず以下のように専用DBへ向けて実行すること：

  ```bash
  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --no-deps backend \
    uv run pytest -m integration tests/integration
  ```

  `docker-compose.test.yml`は`docker-compose.e2e.yml`と同じ「開発用ベース+用途別上書き」のオーバーレイ構成。テストDB自体は`postgres-init/`のスクリプトで自動作成されるが、これはPostgresボリューム初回初期化時のみ有効なため、既存ボリュームに対しては手動で`CREATE DATABASE "$POSTGRES_TEST_DB";`を実行しておくこと。

## 開発用ツール

- `backend/scripts/seed.py`（`uv run python -m scripts.seed`）は、ログインを試すための固定テストユーザーを2人(一般ユーザー `example-user@example.com` と superuser `example-super-user@example.com`)冪等に作成する。本番の superuser は、登録したアカウントを手で昇格させる。**dev専用**（本番DBでは実行しない）。
- `backend/pyproject.toml`の`[tool.pyright]`は、エディタ（VSCode/Pylance）とCLI（`uvx pyright`）の解析ルートを`backend/`に固定する。ワークスペースをどのフォルダで開いても`app`パッケージがfirst-partyとして解決される。`ignore`には現時点の既知の型債務（所有者スコープでの`get_by_id`の上書きなど）を列挙している。

## Docker構成

- `backend/Dockerfile`は`base → builder → runtime`の3段構成。開発時は`builder`ステージ（`--reload`付き）をソースのbindマウントと組み合わせて使い、本番は`runtime`ステージ（非rootユーザー、`--reload`無し）を使う。
- `nginx/nginx.conf`は開発用(平文HTTP、`docker-compose.yml`だけで使う)。本番のTLS終端・HTTP→HTTPSのリダイレクト・証明書の取得と更新は、VPSで複数のプロジェクトが共有するTraefikが担う(`docker-compose.prod.yml`、`OPERATIONS.md`)。
