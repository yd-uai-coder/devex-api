# devex-api

[Devex](https://github.com/yd-uai-coder/devex-project#readme)(AIとの対話で要件を引き出し、要件定義から設計・実装手順・テストまでを、IDでつながった設計情報として組み立てるシステム)のバックエンドです。FastAPI + LangChain(Gemini / Claude)+ PostgreSQL + Redis で構築しています。フロントエンドは [devex-ui](https://github.com/yd-uai-coder/devex-ui#readme) です。

本番: <https://devex.uandi-tech.com/>(API は ConoHa VPS 上の Docker Compose。共有の Traefik の下)

## 主な機能

ルートは `/api/v1` の下にあります(`app/api/routes/`)。

| ルート | 機能 |
|---|---|
| `auth.py` | JWT(アクセストークン + httpOnly Cookie のリフレッシュトークン)による登録・ログイン・更新・ログアウト。Google の ID トークンでのログインと登録・連携 |
| `users.py` | メールアドレス・パスワードの変更、Google の連携と解除、用途(チャット・生成・レビュー・判定)ごとのモデルの選択、API キー(Gemini / Claude)の登録と削除 |
| `projects.py` | プロジェクトの作成(参考資料の添付)・一覧(検索・絞り込み・ページ送り)・削除。ヒアリング(SSE)、観点ごとの状態、4文書の一括生成(バックグラウンド)と進み具合、版の履歴と復元、テスト仕様書 |
| `design_stages.py` | 詳細設計モード: 機能一覧から実装手順書までの段階ごとの生成・編集・承認、陳腐化の検知、変更の影響の分析、詳細設計書・実装手順書の出力 |
| `uml.py` | UML 設計図の意味モデルの取得・更新・検証・自動レイアウト・承認、draw.io / SVG の出力、データ辞書 |
| `reviews.py` | 生成とは別のモデルによる設計レビュー、指摘の採否、レビュー結果からの再ヒアリング |
| `llm.py`・`prompt_templates.py` | 使えるモデルの一覧、プロンプトテンプレートの一覧 |

## アーキテクチャ

```mermaid
flowchart TB
    routes["routes<br/>app/api/routes<br/>要求と応答の変換だけ"]
    services["services<br/>app/services<br/>ユースケース・トランザクションの境界"]
    repos["repositories<br/>app/repositories<br/>永続化(flush のみ)"]
    models["models<br/>app/models<br/>SQLAlchemy ORM"]
    domain["ドメイン(純粋関数)<br/>hearing / documents / detailed_design / uml / review"]
    llm["app/ai/llm<br/>モデルの一覧・クライアントの組み立て"]

    routes --> services
    services --> repos --> models
    services --> domain
    services --> llm
```

| パッケージ | 責務 |
|---|---|
| `app/hearing/` | ヒアリングの観点と選択肢カードの規則 |
| `app/documents/` | 要件定義書・外部設計書・内部設計書・実装計画書・テスト仕様書の意味モデル、ID の採番、検証、Markdown の組み立て |
| `app/detailed_design/` | 詳細設計モードの各段階(機能一覧・データフロー・データモデル・ソフトウェア構造・手順・処理ロジック・実装計画)の生成の指示・検証・組み立て、変更の影響のたどり方、実装手順書 |
| `app/uml/` | 図の意味モデル(DFD・ER・コンポーネント)、生成、検証、自動レイアウト、draw.io / SVG の出力 |
| `app/review/` | 設計レビューの入力・出力・採否・直ったかの判定・使えるかの方針 |
| `app/ai/llm/` | provider・用途・モデルのカタログ、クライアントの組み立てとキャッシュ、キーの確かめ、E2E 用の偽の LLM |

ドメインのパッケージは DB にも LLM にも触りません。サービスがここを呼び、結果を保存します。

## 設計の要点

詳しい「なぜ」は [`CLAUDE.md`](./CLAUDE.md) にあります。

- **例外は1か所に集める** ── ドメイン例外を依存の無い末端のモジュール(`app/services/errors.py`)に集め、循環 import を構造的に避けます。ルートでは `try/except` を書かず、`register_error_handlers` がまとめて JSON の応答(`{detail, code}`)に変えます。認証の境界だけは、失敗の理由を外に漏らさないために、意図して素の `HTTPException` にしています。
- **`commit` はサービスだけ** ── リポジトリは `flush` までにし、トランザクションの単位はサービスが握ります。
- **純粋な規則と副作用を分ける** ── 生成の指示・構造化出力のスキーマ・検証・文書の組み立てはドメインのパッケージの純粋関数にし、DB や LLM のスタブ無しでテストします。
- **LLM の解決** ── `LlmResolver` が「ユーザーと用途 → (provider, モデル, キー)」を決めます。モデルは利用者の選択(無ければ既定値)、キーは利用者のキー(無ければ環境変数の既定のキー)です。利用者のキーは Fernet で暗号化して保存し(`MultiFernet` で鍵を入れ替えられる)、登録の前に費用のかからない呼び出しで有効かを確かめます。
- **生成とレビューを別のモデルにする** ── 生成は Gemini、レビューは Claude Sonnet、直ったかの判定は Claude Haiku が既定です。同じモデルの自己診断では見落としが残るため分けています。既定のキーで流すレビュー・判定は、利用者ごとに1日の回数の上限があります。
- **再試行の切り分け** ── `invoke_with_retry` が、クォータ超過は即座に諦め、混雑(503・529 など)は間を伸ばして再試行し、その他の一時的な失敗は規定回数だけ再試行します。SDK の中の再試行は止め、二重に重ならないようにしています。
- **レート制限** ── Redis のカウンタで、複数の時間の枠(1時間・1日)をまとめて数えます(`INCR` と `EXPIRE NX` を1往復のパイプラインで送り、TTL の無いキーを残しません)。チャット・ログイン(IP とメール)・登録・Google・キーの登録に掛けています。
- **危険な設定は起動時に落とす** ── `ENVIRONMENT=production` で、弱い `JWT_SECRET_KEY`・`DEBUG=true`・E2E 用の偽の LLM / 偽の Google のフラグがあれば、起動を拒否します。本番では Swagger UI・OpenAPI も閉じます。
- **リクエストの保護** ── ボディの上限(413)をアプリ側にも置きます(nginx を通らない経路でも効くように)。

## テスト

| 種類 | 中身 | 件数 |
|---|---|---|
| 単体(`tests/unit/`) | インメモリの SQLite。ドメインの純粋関数は素で、LLM・Redis はフェイクで差し替える | 167ファイル・約1,500ケース |
| 結合(`tests/integration/`) | FastAPI → 実 PostgreSQL → 実 Redis。開発用とは別の**テスト専用の DB** に向ける | 9ファイル |
| E2E | devex-ui の Playwright から、`E2E_FAKE_LLM=true` の偽の LLM で起動する(`docker-compose.e2e.yml`) | devex-ui 側 |

結合テストはテストのたびにテーブルを作り直すので、開発用の DB に向けると開発中のデータが消えます。必ず次のように専用の DB で流してください(理由は `CLAUDE.md`「テストの分離」)。

```bash
cd backend
uv run pytest                        # 単体(既定で integration を除く)
uv run pytest --cov=app --cov-report=term-missing

cd ..
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --no-deps backend \
  uv run pytest -m integration tests/integration
```

## 技術スタック

| 分類 | 技術 |
| --- | --- |
| 言語 / ランタイム | Python 3.13 |
| Web Framework | FastAPI, Uvicorn |
| AI | LangChain, Gemini (`langchain-google-genai`, `google-genai`), Claude (`langchain-anthropic`) |
| DB | PostgreSQL 17, SQLAlchemy 2.x (async, asyncpg), Alembic |
| Cache / State | Redis(レート制限・リフレッシュトークン) |
| 認証 | PyJWT, pwdlib (Argon2), google-auth(Google の ID トークンの検証) |
| 暗号化 | cryptography(Fernet。利用者の API キーの保存) |
| バリデーション | Pydantic v2, pydantic-settings |
| ログ・監視 | structlog(構造化ログ), Sentry(任意) |
| パッケージ管理 | uv |
| テスト | pytest, pytest-asyncio, pytest-mock, pytest-cov, HTTPX |
| Lint | Ruff(`ruff check`) |
| インフラ | Docker, Docker Compose, Traefik(本番), Nginx(開発) |

## ディレクトリ構成

```text
devex-api/
├── CLAUDE.md                       # アーキテクチャ・設計判断の記録
├── OPERATIONS.md                   # デプロイ・運用手順(ConoHa VPS + Traefik + Vercel)
├── .github/workflows/deploy.yml    # CI(lint・単体テスト) + CD(main への push で VPS へ自動デプロイ)
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI のエントリーポイント
│   │   ├── api/                    # ルーティング・DI・エラーハンドラ・ミドルウェア・Cookie
│   │   ├── core/                   # 設定・セキュリティ・暗号・DB 接続・共通例外・ログ
│   │   ├── models/                 # SQLAlchemy ORM
│   │   ├── schemas/                # Pydantic のスキーマ(要求・応答)
│   │   ├── repositories/           # データアクセス(汎用 CRUD の基底クラス)
│   │   ├── services/               # ユースケース・ドメイン例外・レート制限・LLM の解決と再試行
│   │   ├── hearing/ documents/ detailed_design/ uml/ review/   # ドメイン(純粋関数)
│   │   ├── ai/llm/                 # LLM のカタログ・クライアント・偽の LLM
│   │   └── infrastructure/         # Redis・HTTP クライアント
│   ├── alembic/                    # DB マイグレーション
│   ├── tests/{unit,integration,fixtures}/
│   ├── scripts/seed.py             # 開発用のテストユーザーの作成
│   ├── pyproject.toml / uv.lock
│   └── Dockerfile                  # base → builder(開発)→ runtime(本番、非 root)
├── nginx/nginx.conf                # 開発用(平文 HTTP)
├── postgres-init/                  # テスト専用 DB の作成
├── docker-compose.yml              # 開発
├── docker-compose.test.yml         # 結合テスト用の上書き
├── docker-compose.e2e.yml          # E2E 用の上書き(偽の LLM・偽の Google)
└── docker-compose.prod.yml         # 本番(Traefik の下に置く)
```

## 開発環境

必要なもの: [uv](https://docs.astral.sh/uv/)(Python 3.13 は uv が用意する)、Docker / Docker Compose。

```bash
cp .env.example .env          # 値を編集する
docker compose up --build     # API: http://localhost:8000 / Swagger UI: http://localhost:8000/docs
docker compose exec backend uv run alembic upgrade head
docker compose exec backend uv run python -m scripts.seed   # 一般ユーザーと superuser の試用アカウント(開発専用)
```

backend コンテナは `backend/` をバインドマウントし、`--reload` 付きで起動するので、コードの変更がすぐ反映されます。Docker を使わずに起動する場合は、`backend/.env.example` を `backend/.env` にコピーし、`cd backend && uv sync && uv run uvicorn app.main:app --reload` を実行します。

### 環境変数

ルートの `.env.example` は docker compose 用、`backend/.env.example` はホストで直接起動する場合用です(`.env` はコミットしない)。既定値は `backend/app/core/config.py` にあります。

| 分類 | 変数 | 説明 |
| --- | --- | --- |
| アプリ | `ENVIRONMENT` / `DEBUG` | `production` のとき Swagger UI・OpenAPI を閉じ、危険な設定を起動時に拒否する。本番では `DEBUG=false` |
| | `CORS_ORIGINS` | 画面のオリジン(本番は Vercel の URL とカスタムドメイン) |
| | `PROJECT_NAME` / `API_V1_PREFIX` | OpenAPI の表示名と、ルートの接頭辞(既定 `/api/v1`) |
| DB・Redis | `DATABASE_URL` / `REDIS_URL` | 例: `postgresql+asyncpg://user:pass@host:5432/app`、`redis://host:6379/0` |
| | `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_TEST_DB` | ルートの `.env` のみ。Postgres コンテナの認証情報・開発用 DB・結合テスト用 DB |
| 認証 | `JWT_SECRET_KEY` / `JWT_ALGORITHM` | 署名の鍵(本番は32文字以上)とアルゴリズム(既定 `HS256`) |
| | `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | 既定30分・14日。リフレッシュトークンは Redis に JTI ごとに保存する |
| | `GOOGLE_CLIENT_ID` | Google でのログインの OAuth クライアント ID。無ければ Google のログインだけが 503 になる |
| AI(既定のキー) | `GOOGLE_API_KEY` / `GEMINI_MODEL` | Gemini のキーと既定のモデル(既定 `gemini-3.5-flash-lite`) |
| | `ANTHROPIC_API_KEY` / `REVIEW_MODEL` / `JUDGE_MODEL` | 設計レビュー(既定 `claude-sonnet-5-5`)と、直ったかの判定(既定 `claude-haiku-5-5`) |
| | `REVIEW_RATE_LIMIT_PER_DAY` / `JUDGE_RATE_LIMIT_PER_DAY` | 既定のキーで流すレビュー・判定の、利用者ごとの1日の上限 |
| | `REVIEW_MAX_TOKENS` / `JUDGE_MAX_TOKENS` / `JUDGE_MAX_PER_RUN` | レビュー・判定の出力の上限と、1回の生成・承認の後に判定する指摘の数 |
| 利用者のキー | `ENCRYPTION_KEY` | Fernet の鍵(カンマ区切りで複数。先頭で暗号化し、すべてで復号する)。無ければキーの登録だけが 503 になる |
| | `API_KEY_RATE_LIMIT_PER_HOUR` / `API_KEY_CHECK_TIMEOUT_SECONDS` | キーの登録の1時間の上限と、キーを確かめる呼び出しの待ち時間 |
| レート制限 | `CHAT_RATE_LIMIT_PER_HOUR` / `CHAT_RATE_LIMIT_PER_DAY` | チャットの送信(既定 20回/時・100回/日) |
| | `LOGIN_RATE_LIMIT_PER_IP_PER_HOUR` / `LOGIN_RATE_LIMIT_PER_EMAIL_PER_HOUR` / `REGISTER_RATE_LIMIT_PER_IP_PER_HOUR` / `GOOGLE_RATE_LIMIT_PER_IP_PER_HOUR` | 総当たり・大量登録の対策 |
| | `MAX_REQUEST_BODY_BYTES` | ボディの上限(既定 2MB。超えたら 413) |
| 監視 | `SENTRY_DSN` | 設定したときだけ Sentry を初期化する |
| E2E 専用 | `E2E_FAKE_LLM` / `E2E_FAKE_GOOGLE` | 偽の LLM・偽の Google の credential。本番では起動時に拒否する |
| ポート | `BACKEND_PORT` / `NGINX_PORT` / `POSTGRES_PORT` / `REDIS_PORT` | ルートの `.env` のみ。開発時にホストへ公開するポート |

### マイグレーション

```bash
cd backend
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "message"
uv run alembic heads           # head が1つであることを確かめる
```

### Lint

```bash
cd backend
uv run ruff check .
```

`ruff format` は使っていません(既存のファイルの整形が大きく変わるため)。

## 本番・CI/CD

- **CI/CD**: `.github/workflows/deploy.yml` が push と PR のたびに lint と単体テストを流し、`main` への push では VPS へ SSH して `git pull`・コンテナの再ビルド・`alembic upgrade head` を行います。
- **本番の構成**: `docker-compose.prod.yml` で、`--reload` 無しの Uvicorn(Dockerfile の `runtime` ステージ、非 root)を動かします。backend はホストにポートを公開せず、VPS で複数のプロジェクトが共有する **Traefik**(TLS の終端・HTTPS へのリダイレクト・証明書の自動更新)の下からだけ届きます。PostgreSQL・Redis は Docker の内部ネットワークだけに置きます。
- 手順(初回の構築・Secrets・バックアップ・ロールバック・Vercel 側の設定・Google の OAuth の設定)は [`OPERATIONS.md`](./OPERATIONS.md) にあります。

## 既知の制約

- パスワードのリセットとメールアドレスの確認は未実装です。

## 出自

自作の FastAPI + LangChain のテンプレートから作り始めました。層の構成・エラー処理・レート制限・テストの分離はテンプレートから引き継いだものです。テンプレートの汎用チャット(`POST /chat`・LangGraph・Tavily)は Devex で使わないため削除しました。
