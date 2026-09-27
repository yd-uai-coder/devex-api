# devex-api

[Devex](../README.md)(AIとの対話でヒアリングを行い、要件定義書・外部設計書・内部設計書・実装計画書の4種Markdownドキュメントを自動生成するシステム)のバックエンドです。FastAPI + LangChain + PostgreSQL + Redisで構築しています。

## Devexとしての主な機能

- **認証**(`app/api/routes/auth.py`) — JWT(アクセストークン+リフレッシュトークン)によるユーザー登録・ログイン・トークン更新
- **プロジェクト管理**(`app/api/routes/projects.py`、`projects`テーブル) — プロジェクトの作成(ヒアリング用の初期情報入力+任意の資料添付、`intake_files`テーブル)・一覧・詳細取得
- **チャットヒアリング**(`chat_histories`テーブル) — プロジェクトごとにAI(Gemini)と対話形式でヒアリングを行い、SSEで応答をストリーミングする(`ChatService`、`app/services/chat_service.py`)
- **ドキュメント生成**(`generated_documents`テーブル) — ヒアリング内容から4種のドキュメントを順に生成する(`DocGeneratorService`、`app/services/doc_generator_service.py`)。文書ごとにバージョン管理(直近3件保持)し、Markdown形式でダウンロード可能

設計判断の背景（なぜエラーを1箇所に集約しているか、なぜリポジトリ層は`flush`のみか等）は [`CLAUDE.md`](./CLAUDE.md) にまとめています。デプロイ・運用手順は [`OPERATIONS.md`](./OPERATIONS.md) を参照してください。


## 技術スタック

| 分類 | 技術 |
| --- | --- |
| 言語 / ランタイム | Python 3.13 |
| Web Framework | FastAPI, Uvicorn |
| AI | LangChain, LangGraph, Gemini (`langchain-google-genai`)|
| DB | PostgreSQL, SQLAlchemy 2.x (async), Alembic |
| Cache / State | Redis |
| 認証 | PyJWT, pwdlib (Argon2) |
| バリデーション | Pydantic v2, pydantic-settings |
| パッケージ管理 | uv |
| テスト | pytest, pytest-asyncio, pytest-mock, pytest-cov, HTTPX |
| Lint / Format | Ruff |
| インフラ | Docker, Docker Compose, Nginx |

## ディレクトリ構造

```text
project-root/
├── CLAUDE.md                       # アーキテクチャ全体像・設計判断の記録
├── OPERATIONS.md                   # デプロイ・運用手順(ConoHa VPS + Vercel)
├── .github/workflows/deploy.yml    # CI(lint/test) + CD(mainへのpush時にVPSへ自動デプロイ)
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPIエントリーポイント
│   │   ├── api/                   # ルーティング + DI + エラーハンドラ登録
│   │   ├── core/                  # 設定 / セキュリティ / DB接続 / 共通例外(AppError)
│   │   ├── models/                # SQLAlchemy ORM
│   │   ├── schemas/                # Pydantic Schema（LLM構造化出力スキーマ含む）
│   │   ├── services/               # ユースケース層 / ドメイン例外 / レート制限
│   │   ├── repositories/           # データアクセス層（汎用CRUD基底クラス）
│   │   ├── ai/                     # LangChain / LangGraph / Gemini / Tavily
│   │   └── infrastructure/         # Redis / HTTPクライアント
│   ├── alembic/                    # DBマイグレーション
│   ├── tests/{unit,integration,fixtures}/
│   ├── pyproject.toml / uv.lock
│   └── Dockerfile
├── nginx/nginx.conf                # 開発用（平文HTTP）
├── nginx/nginx.prod.conf           # 本番用（HTTPSリダイレクト + TLS終端 + certbot対応）
├── docker-compose.yml              # 開発環境
├── docker-compose.prod.yml         # 本番環境
├── .env.example                    # docker-compose用
└── backend/.env.example            # ホスト上で直接起動する場合用
```

## 必要な環境

- Python 3.13（`uv`が自動で用意するため手動インストールは不要）
- [uv](https://docs.astral.sh/uv/)
- Docker / Docker Compose（コンテナで動かす場合）

## uvのセットアップ

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh

cd backend
uv sync   # pyproject.toml / uv.lock から依存関係を再現
```

## 環境変数

- ルートの `.env.example` … `docker-compose` で使用（Postgres/Redis の認証情報 + バックエンドへ渡す設定）
- `backend/.env.example` … `backend/` 直下で `uv run uvicorn ...` のようにホスト上で直接起動する場合用

いずれも `.env` にコピーして値を埋めてください（`.env` はコミットしないでください）。

| 変数 | 説明 |
| --- | --- |
| `ENVIRONMENT` | `development` / `production` など。`production`時はSwagger UI・ReDoc・OpenAPIスキーマを自動的に非公開にします |
| `DATABASE_URL` | 例: `postgresql+asyncpg://user:pass@host:5432/app` |
| `REDIS_URL` | 例: `redis://host:6379/0` |
| `JWT_SECRET_KEY` | JWT署名用シークレット（32byte以上推奨） |
| `JWT_ALGORITHM` | 既定 `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access Token有効期限（分） |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh Token有効期限（日）。RedisにJTI単位で保存されます |
| `GOOGLE_API_KEY` | Gemini用APIキー |
| `TAVILY_API_KEY` | Tavily検索用APIキー |
| `GEMINI_MODEL` | 既定 `gemini-3.5-flash-lite`(`gemini-2.5-flash-lite`は新規利用不可のため移行済み、[`OPERATIONS.md`](./OPERATIONS.md)参照) |
| `CHAT_RATE_LIMIT_PER_HOUR` | チャットメッセージ送信のレート制限（1時間あたり、既定 `20`）。超過時は429を返します |
| `CHAT_RATE_LIMIT_PER_DAY` | チャットメッセージ送信のレート制限（1日あたり、既定 `100`） |
| `LOGIN_RATE_LIMIT_PER_IP_PER_HOUR` | ログインのレート制限（IPあたり・1時間、既定 `30`）。総当たり対策 |
| `LOGIN_RATE_LIMIT_PER_EMAIL_PER_HOUR` | ログインのレート制限（メールアドレスあたり・1時間、既定 `20`） |
| `REGISTER_RATE_LIMIT_PER_IP_PER_HOUR` | ユーザー登録のレート制限（IPあたり・1時間、既定 `10`）。大量登録対策 |
| `MAX_REQUEST_BODY_BYTES` | リクエストボディの上限バイト数（既定 `2000000`）。超過時は413を返します |
| `DEBUG` | 本番では必ず `false` |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | ルートの`.env.example`のみ。docker-composeが起動するPostgresコンテナの認証情報・DB名 |
| `BACKEND_PORT` / `NGINX_PORT` | ルートの`.env.example`のみ。開発時にホストへ公開するポート（`docker-compose.prod.yml`では`BACKEND_PORT`は使用しません） |

## 開発環境

### Docker Composeで起動

```bash
cp .env.example .env   # 値を編集してから

docker compose up --build
```

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- Nginx経由のヘルスチェック: http://localhost/health

backendコンテナは `backend/` をバインドマウントし、`--reload` 付きUvicornで起動するため、コード変更が即座に反映されます（`.venv` は名前付きボリュームで分離しているためホストの仮想環境と衝突しません）。

### Dockerを使わずホストで直接起動

```bash
cd backend
cp .env.example .env   # localhostのPostgres/Redisを指す値に編集
uv run uvicorn app.main:app --reload
```

## DB Migration

```bash
cd backend
uv run alembic upgrade head          # マイグレーション適用
uv run alembic revision --autogenerate -m "message"   # 新規マイグレーション作成
```

Docker Compose経由で実行する場合:

```bash
docker compose exec backend uv run alembic upgrade head
```

## テスト実行方法

```bash
cd backend
uv run pytest                        # unit tests（既定でintegrationは除外）
uv run pytest -m integration         # PostgreSQL/Redisが起動している状態で結合テスト
uv run pytest --cov=app --cov-report=term-missing   # カバレッジ付き
```

結合テスト (`tests/integration/`) はFastAPI → 実PostgreSQL → 実Redisを実際に使用するため、`docker compose up postgres redis` などで両方を起動した状態で実行してください。Gemini/TavilyはUnit Testでは全てMockに置き換えています（`tests/unit/test_ai_graph_nodes.py`）。

## Ruff実行方法

```bash
cd backend
uv run ruff check .      # Lint
uv run ruff format .     # Format
```

## 本番環境

実際にConoHa VPS等へデプロイする際の手順（TLS証明書の取得・CORS設定・バックアップ・ロールバック・Vercel側のdevex-uiデプロイ含む）は [`OPERATIONS.md`](./OPERATIONS.md) を参照してください。以下はローカルでの本番相当環境の起動のみを示します。

```bash
cp .env.example .env     # 本番用の値（強固なパスワード・シークレット）を設定
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend uv run alembic upgrade head
```

`docker-compose.prod.yml` では以下を行っています。

- `--reload`を使用しない本番用Uvicorn起動
- backendコンテナのポートをホストに公開せず、Nginxのみを外部公開の入口とする
- PostgreSQL/RedisはDocker内部ネットワークのみに限定し、ポートを公開しない
- PostgreSQLデータは名前付きVolumeで永続化
- `ENVIRONMENT=production`が設定されるため、Swagger UI・ReDoc・OpenAPIスキーマは自動的に非公開になります
- backend/Dockerfileの`HEALTHCHECK`を使い、`backend`→`nginx`の起動順を`condition: service_healthy`で制御します
- Nginxは`nginx/nginx.prod.conf`（80→443へのリダイレクト + TLS終端 + certbotの`/.well-known/acme-challenge/`対応）を使用します。有効化するには`nginx/certs/`に`fullchain.pem`・`privkey.pem`を配置してください。証明書自体の発行・更新は`certbot`サービス（`profiles: ["certbot"]`、通常の`up`では起動しません）で行います。手順は[`OPERATIONS.md`](./OPERATIONS.md)「TLS証明書の取得・更新」参照

## CI/CD

`.github/workflows/deploy.yml`が、push/PR時のlint・unit test実行と、`main`ブランチへのpush時のConoHa VPSへの自動デプロイ(SSH経由で`git pull`+コンテナ再ビルド)を行います。必要なGitHub Secretsの設定手順は[`OPERATIONS.md`](./OPERATIONS.md)「GitHub Actionsによる自動デプロイ」参照。

## 未実装・今後対応が必要な事項

- 認証API（登録・ログイン・リフレッシュ・ログアウト）の基盤は実装済みですが、パスワードリセットやメール確認などの拡張は未実装です
- LangGraphのワークフローは検索要否判定が簡易的なダミー実装です。実運用では`draft_response`内の判定ロジックを強化してください
- 本番用HTTPS証明書の更新は`certbot`サービス＋cron/systemd timerによる手動運用（[`OPERATIONS.md`](./OPERATIONS.md)参照）であり、完全自動化（renewal hookでのnginx reload連携等）はしていません
- `prompt_templates`テーブルはモデル・マイグレーションのみ存在し、repository/service/route/UIいずれも未実装のまま放置されています(プロンプトテンプレート選択機能を実装するか、テーブル自体を削除するかは未定)
- テンプレート由来の汎用チャット機能(`conversations`/`messages`テーブル、`app/services/chat.py`、`POST /chat`)が、Devex本来のヒアリングチャット(`chat_histories`、`ChatService`)と並存したまま残っています
- ドキュメント生成(`DocGeneratorService`)は`app/ai/graph/`のLangGraph `StateGraph`を使わず、手続き的な`llm.ainvoke()`の逐次呼び出しで実装されています。LangGraphワークフロー自体はDevexの実機能からは現状未使用です
