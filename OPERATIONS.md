# 運用マニュアル(デプロイ・バックアップ・監視)

このドキュメントは、`devex-api`(FastAPI + PostgreSQL + Redis)をConoHa VPS(生VPS。マネージドPaaSではなく自前でDocker運用)へ、`devex-ui`(Next.js)をVercelへデプロイ・運用するための手順書。個人開発規模を想定しており、エンタープライズ級の監視基盤等は求めない。

作成の背景・検証内容は [`textbook/Phase-5/Phase-5-introduction.md`](../textbook/Phase-5/Phase-5-introduction.md) を参照。

## 全体構成

```
[ ブラウザ ] → [ Vercel: devex-ui (Next.js) ]
                       │  NEXT_PUBLIC_API_URL 経由でAPIを叩く
                       ▼
              [ ConoHa VPS ]
              ┌─────────────────────────────┐
              │ nginx (80/443、TLS終端)        │
              │  └─ backend (FastAPI, 8000)    │
              │       ├─ postgres              │
              │       └─ redis                 │
              └─────────────────────────────┘
```

## 1. ConoHa VPS 初期セットアップ

1. VPS に Docker Engine + Docker Compose plugin を導入する(ConoHaのOSテンプレートにDockerが同梱されていない場合、[Docker公式インストール手順](https://docs.docker.com/engine/install/)に従う)。
2. ファイアウォール(ConoHaコントロールパネルの「セキュリティグループ」またはVPS内`ufw`)で `80`(証明書のHTTP-01チャレンジ・HTTPSへのリダイレクト用)と `443` のみを外部公開する。`5432`(postgres)・`6379`(redis)・`8000`(backend)は`docker-compose.prod.yml`側でも外部公開していないため、VPSのファイアウォールでも開けない。
3. VPS上でリポジトリを配置する: `git clone <devex-apiのリモートURL> && cd devex-api`。

## 2. 環境変数の管理方針

- `devex-api/.env`(root。`docker-compose.prod.yml`が`env_file`経由で読む)は**VPS上にのみ実体を置き、gitにコミットしない**(`.gitignore`で既に除外済み)。`.env.example`を土台に値を埋める。
- 本番相当にする際に特に注意する項目:
  - `ENVIRONMENT=production` / `DEBUG=false` ── `docker-compose.prod.yml`の`backend`サービスが`environment:`で強制上書きするため、`.env`側の値に関わらず必ずこの値になる。ただし`JWT_SECRET_KEY`が32文字未満、または`change-me`で始まる場合は`app/core/config.py`の起動時バリデーションで拒否される(`ENVIRONMENT=production`時のみ)。
  - `GOOGLE_API_KEY` ── 実際のGemini APIキー。Phase 5-2で確認済みの通り、`GEMINI_MODEL`は`gemini-3.5-flash-lite`を使うこと(`gemini-2.5-flash-lite`は新規利用不可、404 NOT_FOUND)。
  - `CORS_ORIGINS` ── **Vercelの本番URL(例: `https://devex-ui.vercel.app`、カスタムドメインを設定した場合はそちらも)を含める**。未設定時のデフォルトは`["http://localhost:3000"]`のみで、Vercel上のdevex-uiからのAPI呼び出しがすべてCORSエラーになるため、初回デプロイ前に必ず設定する。
  - `E2E_FAKE_LLM` ── `.env`に**書かない、または`false`のままにする**(起動時バリデーションが`ENVIRONMENT=production`かつ`E2E_FAKE_LLM=true`を拒否する)。

## 3. 初回デプロイ手順(devex-api)

```bash
cd devex-api
# .env を用意済みであることを前提とする(上記2.参照)
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend uv run alembic upgrade head
docker compose -f docker-compose.prod.yml ps   # 4サービスすべて healthy になることを確認
curl -k https://localhost/health              # 証明書取得前は自己署名/未設定でエラーになる点に注意(後述4を先に行う)
```

初回は証明書がまだ無いため`nginx`の起動順序に注意する: `nginx.prod.conf`は`ssl_certificate`が`./nginx/certs/fullchain.pem`を参照するため、**このファイルが存在しないと`nginx`コンテナが起動に失敗する**。そのため実際の初回手順は次のいずれかを取る:

- (推奨)先に一時的な自己署名証明書を`./nginx/certs/`に置いて`nginx`を起動できる状態にし、下記4節の手順で本物の証明書に差し替える。
  ```bash
  mkdir -p nginx/certs
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout nginx/certs/privkey.pem -out nginx/certs/fullchain.pem \
    -days 1 -subj "/CN=your-domain.example.com"
  ```
- または`nginx`だけ`up`から一時的に除外して`backend`/`postgres`/`redis`のみ先に起動し、証明書取得後に`nginx`を起動する。

## 4. TLS証明書の取得・更新(Let's Encrypt / certbot)

`docker-compose.prod.yml`の`certbot`サービスは`profiles: ["certbot"]`で通常の`up`時には起動しない、証明書取得・更新専用のワンショットサービス。

### 初回取得

```bash
# 1. nginxがACMEチャレンジ(/.well-known/acme-challenge/)を捌ける状態で起動していること(上記3節)
docker compose -f docker-compose.prod.yml --profile certbot run --rm certbot \
  certonly --webroot -w /var/www/certbot \
  -d your-domain.example.com \
  --email you@example.com --agree-tos --no-eff-email

# 2. certbotが発行した証明書は certbot_conf という名前付きボリューム内の
#    /etc/letsencrypt/live/your-domain.example.com/ に格納される。
#    nginx.prod.conf が参照する ./nginx/certs/ (フラットな2ファイル)へコピーする。
docker run --rm \
  -v devex-api_certbot_conf:/etc/letsencrypt:ro \
  -v "$(pwd)/nginx/certs:/out" \
  alpine sh -c "cp /etc/letsencrypt/live/your-domain.example.com/fullchain.pem /etc/letsencrypt/live/your-domain.example.com/privkey.pem /out/"

# 3. nginxに新しい証明書を読み込ませる
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
```

`devex-api_certbot_conf`の`devex-api_`プレフィックスはdocker composeのプロジェクト名(既定はディレクトリ名)。`docker volume ls`で実際の名前を確認できる。

### 更新(renewal)

Let's Encryptの証明書は90日で失効するため、cron等で定期的に更新する。ConoHa VPS上の`crontab -e`に以下を追加する例(毎月1日 午前3時):

```cron
0 3 1 * * cd /path/to/devex-api && \
  docker compose -f docker-compose.prod.yml --profile certbot run --rm certbot renew --webroot -w /var/www/certbot && \
  docker run --rm -v devex-api_certbot_conf:/etc/letsencrypt:ro -v /path/to/devex-api/nginx/certs:/out alpine \
    sh -c "cp /etc/letsencrypt/live/*/fullchain.pem /etc/letsencrypt/live/*/privkey.pem /out/" && \
  docker compose -f docker-compose.prod.yml exec nginx nginx -s reload >> /var/log/devex-certbot-renew.log 2>&1
```

## 5. バックアップ(PostgreSQL)

個人開発規模のため、`pg_dump`をcronで定期実行し、VPS外(手元PC・オブジェクトストレージ等)に保存する運用で十分とする。

```bash
# 例: 毎日午前4時にダンプを取得し、VPS上に7世代だけ残す
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup_$(date +%F).sql
find . -name 'backup_*.sql' -mtime +7 -delete
```

取得したダンプは`scp`等で定期的にVPS外へコピーすること(VPS自体の障害に対するバックアップにならないため)。

## 6. ロールバック手順

- アプリケーションコード: **9節の自動デプロイ導入後は、`main`ブランチを問題のコミットの直前まで`git revert`してpushするのが基本**(自動デプロイパイプラインがそのまま最新状態としてVPSへ反映する)。緊急時は前のgitタグ/コミットへVPS上で直接`git checkout`し、`docker compose -f docker-compose.prod.yml up -d --build`で再ビルド・再起動する手動手順も引き続き使える。
- DBマイグレーション: `alembic downgrade <直前のrevision>`。ダウングレードで対応できない破壊的変更(カラム削除等)を含むマイグレーションは、ロールバック前に上記5節のバックアップから復元することを優先する。

## 7. 監視の最低限

エンタープライズ級の監視基盤は導入せず、以下で足りるとする:

```bash
docker compose -f docker-compose.prod.yml ps        # 4サービスの STATUS/HEALTH 列を確認
curl -s https://your-domain.example.com/health       # {"status":"ok","database":"ok","redis":"ok"} を期待
docker compose -f docker-compose.prod.yml logs -f backend   # アプリログの確認
```

`backend`は`backend/Dockerfile`にHEALTHCHECK命令を組み込み済みのため、コンテナ単体でも`docker compose ps`のSTATUS列に`(healthy)`/`(unhealthy)`が表示される(Phase 5-1で追加)。

## 8. devex-ui(Vercel)デプロイ手順

1. [Vercel](https://vercel.com)でアカウント作成後、devex-uiのGitHubリポジトリをインポートする(Next.jsは自動検出されるため`vercel.json`等の追加設定は不要)。
2. プロジェクトの環境変数に `NEXT_PUBLIC_API_URL` を設定する(値: ConoHa VPS側のAPIの公開URL、例 `https://your-domain.example.com`)。
3. デプロイ後、Vercelが割り当てたURL(または設定したカスタムドメイン)を、devex-api側の`.env`の`CORS_ORIGINS`に追加し、`docker compose -f docker-compose.prod.yml up -d`でbackendを再起動して反映する。
4. 動作確認: Vercelのデプロイ済みURLからログイン→プロジェクト作成→チャットヒアリング→ドキュメント生成の一連が通ることを確認する。

以降、`main`ブランチへのpushでVercelが自動的に再デプロイする(Gitリポジトリ接続時点で有効になる標準機能。追加設定不要)。

## 9. GitHub Actionsによる自動デプロイ

`devex-api`・`devex-ui`それぞれのリポジトリに、push/PR時のCI(lint・test)を実行するGitHub Actionsワークフローがある。デプロイの自動化は**片方のみ**自前で組んでいる:

| リポジトリ | ワークフロー | CI(lint/test) | デプロイ |
| :--- | :--- | :--- | :--- |
| `devex-api` | `.github/workflows/deploy.yml` | あり(`ruff check` + `pytest -m "not integration"`) | **あり**: CI成功後、`main`へのpush時にConoHa VPSへSSHデプロイ |
| `devex-ui` | `.github/workflows/ci.yml` | あり(`lint` + `test` + `build`) | なし(上記8節のVercelネイティブ連携に委ねる) |

`devex-ui`はVercelのGitHub連携が既にデプロイを担うため、Actions側に重ねてデプロイジョブを追加していない(Vercel連携を無効化して`vercel` CLI + トークンでActions駆動デプロイに切り替えることも可能だが、個人開発規模でその複雑さに見合うメリットが無いため見送った)。

### `devex-api`: 自動デプロイの前提

このワークフローは**2回目以降の更新反映を自動化するもの**であり、初回セットアップ(1〜4節)自体は自動化しない。導入前提として、VPS上で以下が完了していること:

- リポジトリが`git clone`済みで、`.env`が配置済み(2節)
- 初回手動デプロイ(3節)が完了し、`docker compose -f docker-compose.prod.yml`のスタックが動作していること
- SSHでVPSへ接続可能なキーペアが用意されていること(ワークフロー専用の鍵を新規作成することを推奨。既存の個人ログイン用鍵の使い回しは避ける)

### 必要なGitHub Secrets(`devex-api`リポジトリ)

GitHubリポジトリの Settings → Secrets and variables → Actions → New repository secret から、以下を登録する。

| Secret名 | 値 |
| :--- | :--- |
| `VPS_HOST` | ConoHa VPSのIPアドレスまたはホスト名 |
| `VPS_USER` | SSHログインユーザー名 |
| `VPS_SSH_KEY` | 上記ユーザーで`git pull`・`docker compose`が実行できる秘密鍵(PEM形式)の中身。対応する公開鍵をVPSの`~/.ssh/authorized_keys`に登録しておく |
| `VPS_DEVEX_API_PATH` | VPS上で`devex-api`リポジトリをcloneした絶対パス(例: `/home/deploy/devex-api`) |

設定後は、`main`ブランチへのpush(マージ含む)のたびに、lint・testが通れば自動的にVPSへ`git pull`+コンテナ再ビルド+マイグレーション適用が行われる。手動デプロイ(3節)は初回セットアップ時、または自動デプロイが使えない状況(SSH鍵の再発行中等)の代替手段として引き続き使える。
