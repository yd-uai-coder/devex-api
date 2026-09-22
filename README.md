# FastAPI + LangChain + LangGraph Template

FastAPI・LangChain・LangGraph・PostgreSQL・Redisを組み合わせた、AIチャットバックエンドの開発テンプレートです。JWT認証基盤とLangGraphによる`User → Gemini → Tavily → 検索結果評価 → Gemini → 最終回答`のワークフローに加え、`AppError`による統一エラーハンドリングとRedisベースのレート制限を備えています。

設計判断の背景（なぜエラーを1箇所に集約しているか、なぜリポジトリ層は`flush`のみか等）は [`CLAUDE.md`](./CLAUDE.md) にまとめています。

## 技術スタック

| 分類 | 技術 |
| --- | --- |
| 言語 / ランタイム | Python 3.13 |
| Web Framework | FastAPI, Uvicorn |
| AI | LangChain, LangGraph, Gemini (`langchain-google-genai`), Tavily (`langchain-tavily`) |
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
| `GEMINI_MODEL` | 既定 `gemini-2.5-flash` |
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
- Nginxは`nginx/nginx.prod.conf`（80→443へのリダイレクト + TLS終端 + certbotの`/.well-known/acme-challenge/`対応）を使用します。有効化するには`nginx/certs/`に`fullchain.pem`・`privkey.pem`を配置してください（証明書自体の発行・更新（certbotの実行）は本テンプレートには含まれていないため、別途用意する必要があります）

## 未実装・今後対応が必要な事項

- 認証API（登録・ログイン・リフレッシュ・ログアウト）の基盤は実装済みですが、パスワードリセットやメール確認などの拡張は未実装です
- LangGraphのワークフローは検索要否判定が簡易的なダミー実装です。実運用では`draft_response`内の判定ロジックを強化してください
- 本番用HTTPS証明書の発行・自動更新（certbotの実行）は自動化されておらず、別途スクリプトやCronの用意が必要です
