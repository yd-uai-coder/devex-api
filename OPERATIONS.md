# 運用マニュアル(デプロイ・バックアップ・監視)

このドキュメントは、`devex-api`(FastAPI + PostgreSQL + Redis)をConoHa VPS(生VPS。マネージドPaaSではなく自前でDocker運用)へ、`devex-ui`(Next.js)をVercelへデプロイ・運用するための手順書。個人開発規模を想定しており、エンタープライズ級の監視基盤等は求めない。

作成の背景・検証内容は [`textbook/Phase-5/Phase-5-introduction.md`](../textbook/Phase-5/Phase-5-introduction.md) を参照。

## 全体構成

VPS上には複数プロジェクトが同居する前提のため、ポート80/443は**共有のTraefik**(devex-apiのリポジトリに属さない、VPS共通のリバースプロキシ)のみが公開する。各プロジェクトの`backend`等はTraefikと同じDockerネットワーク(`edge`)経由でのみ到達し、自身ではポートを公開しない。

```
[ ブラウザ ] → [ Vercel: devex-ui (Next.js) ]
                       │  NEXT_PUBLIC_API_URL 経由でAPIを叩く
                       ▼
              [ ConoHa VPS ]
              ┌───────────────────────────────────────────┐
              │ Traefik (80/443、TLS終端+自動証明書、VPS共有)     │
              │  ├─ devex-api: backend (FastAPI, 8000)         │
              │  │    ├─ postgres                             │
              │  │    └─ redis                                │
              │  └─ (同居する他プロジェクト...)                     │
              └───────────────────────────────────────────┘
```

Traefikはdocker-composeの`nginx`+`certbot`をまとめて置き換える(TLS終端・HTTP→HTTPSリダイレクト・証明書の自動取得/更新をすべてTraefik側が担う)。devex-api自身は`docker-compose.prod.yml`にnginx/certbotサービスを持たない。

## 1. ConoHa VPS 初期セットアップ

1. VPS に Docker Engine + Docker Compose plugin を導入する(ConoHaのOSテンプレートにDockerが同梱されていない場合、[Docker公式インストール手順](https://docs.docker.com/engine/install/)に従う)。
2. ファイアウォール(ConoHaコントロールパネルの「セキュリティグループ」またはVPS内`ufw`)で `80`(証明書のHTTP-01チャレンジ・HTTPSへのリダイレクト用)と `443` のみを外部公開する。`5432`(postgres)・`6379`(redis)・`8000`(backend)は`docker-compose.prod.yml`側でも外部公開していないため、VPSのファイアウォールでも開けない。
3. **共有Traefikを導入する**(VPSに1つだけ。既に導入済みなら省略):

   ```bash
   sudo mkdir -p /opt/traefik/letsencrypt
   sudo touch /opt/traefik/letsencrypt/acme.json
   sudo chmod 600 /opt/traefik/letsencrypt/acme.json
   ```

   `/opt/traefik/docker-compose.yml`を以下の内容で作成する(`you@example.com`は実際の連絡先メールアドレスに置き換える。Let's Encryptからの証明書失効通知等に使われる):

   ```yaml
   services:
     traefik:
       image: traefik:v3.6
       restart: unless-stopped
       command:
         - "--providers.docker=true"
         - "--providers.docker.exposedbydefault=false"
         - "--entrypoints.web.address=:80"
         - "--entrypoints.websecure.address=:443"
         - "--entrypoints.web.http.redirections.entrypoint.to=websecure"
         - "--entrypoints.web.http.redirections.entrypoint.scheme=https"
         - "--certificatesresolvers.letsencrypt.acme.httpchallenge=true"
         - "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web"
         - "--certificatesresolvers.letsencrypt.acme.email=you@example.com"
         - "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json"
       ports:
         - "80:80"
         - "443:443"
       volumes:
         - /var/run/docker.sock:/var/run/docker.sock:ro
         - ./letsencrypt:/letsencrypt
       networks:
         - edge

   networks:
     edge:
       name: edge
   ```

   `--providers.docker.exposedbydefault=false`により、`traefik.enable=true`labelを明示的に持つコンテナのみがルーティング対象になる(意図しないコンテナが誤って公開されるのを防ぐ)。`networks.edge.name: edge`で、compose のプロジェクト名に関わらずネットワーク名が確実に`edge`になるようにしている(各プロジェクト側の`external: true`参照と一致させるため)。

   > **既知の注意点(実際のConoHa VPSデプロイで発生・解決済み)**
   > - **イメージタグは`v3.6`以上を使うこと**: Docker Engine 29以降はDocker APIの最小サポートバージョンが引き上げられており、`traefik:v3.3`以前のイメージは古いDockerクライアント(APIバージョン1.24固定)を使うため`client version 1.24 is too old`エラーで無限リトライしコンテナを一切検出できなくなる。`v3.6`以降はAPIバージョンの自動ネゴシエーションに対応済み。
   > - **`DOCKER_API_VERSION`環境変数では回避できない**: Traefikの内部Dockerクライアントはこの環境変数を参照しないため、上記の問題はイメージタグを上げる以外に解決方法が無い。

   ```bash
   cd /opt/traefik
   docker compose up -d
   docker compose ps   # traefikがUpになることを確認
   ```

   このTraefikはdevex-apiだけでなく、今後追加する他のプロジェクトとも共有する。新規プロジェクトの追加方法は4節「新規プロジェクトをTraefik配下に追加する手順」を参照(既に同居している別プロジェクトをこの構成へ移行する手順も同じ節にある)。
4. VPS上でdevex-apiリポジトリを配置する: `git clone <devex-apiのリモートURL> && cd devex-api`。

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
# edge ネットワーク(1節でTraefikが作成済み)が無いと backend の起動に失敗する点に注意
# マイグレーションを、backend の起動より前に流す(9節の自動デプロイと同じ順。Phase 24)
docker compose -f docker-compose.prod.yml build backend
docker compose -f docker-compose.prod.yml run --rm backend uv run alembic upgrade head
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps   # backend/postgres/redis が healthy になることを確認
curl -sI https://your-domain.example.com/      # Traefikが自動取得した証明書でHTTPS応答することを確認
```

TLS証明書の取得・更新はTraefik側が自動で行うため(4節参照)、devex-api側での証明書関連の作業は不要。

## 4. 新規プロジェクトをTraefik配下に追加する手順

この節は**devex-apiに限らず、今後Traefik配下へ追加する全プロジェクトに共通して使える汎用手順**。devex-api自体もこの手順の最初の適用例であり、既に同居していた別プロジェクトをTraefik配下へ移行する際も同じ手順を使う。

### 手順(共通テンプレート)

1. 対象プロジェクトの`docker-compose.prod.yml`(または相当のcompose定義)から、ポート公開(`ports: - "80:80"` / `"443:443"`等)を**削除する**。
2. 該当サービスに、以下を追加する(`<router名>`はプロジェクトを識別する任意の名前、他プロジェクトと重複しないこと。`<ドメイン>`は対象プロジェクトのドメイン、`<ポート>`はコンテナ内でアプリが listen しているポート):

   ```yaml
   services:
     app:  # 対象サービス名に読み替え
       networks:
         - edge          # 既存のネットワークに加えて追加
       labels:
         - "traefik.enable=true"
         - "traefik.docker.network=edge"
         - "traefik.http.routers.<router名>.rule=Host(`<ドメイン>`)"
         - "traefik.http.routers.<router名>.entrypoints=websecure"
         - "traefik.http.routers.<router名>.tls.certresolver=letsencrypt"
         - "traefik.http.services.<router名>.loadbalancer.server.port=<ポート>"

   networks:
     edge:
       external: true
   ```

   **`traefik.docker.network=edge`は省略しないこと**: 対象サービスが(`internal`等)`edge`以外のネットワークにも参加している場合、この指定が無いとTraefikがどのネットワーク経由で到達すべきか判断できず、誤ったネットワーク側のIPで接続を試みて`504 Gateway Timeout`になる(実際のConoHa VPSデプロイで発生・解決済み)。対象サービスが`edge`ネットワークのみにしか参加していない場合は省略しても動作するが、事故防止のため常に明示することを推奨する。

3. 対象ドメインのDNS Aレコード(・AAAAレコード)がこのVPSのIPを指していることを確認する。
4. 再起動する: `docker compose -f docker-compose.prod.yml up -d`(ポート公開を外したことで既存の`ports:`設定が変わるため、コンテナの再作成を伴う)。
5. **動作確認(必須)**:
   ```bash
   docker compose -f docker-compose.prod.yml ps                 # 対象サービスがhealthy/upになっていることを確認
   curl -sI https://<ドメイン>/                                    # 200等の正常な応答、TLS証明書が有効であることを確認
   docker compose -f /opt/traefik/docker-compose.yml logs traefik --tail 50   # <ドメイン>宛のルーティング・証明書取得ログにエラーが無いことを確認
   ```

devex-apiの場合、`<router名>`は`devex-api`、`<ドメイン>`は実際のAPI用ドメイン、`<ポート>`は`8000`(`docker-compose.prod.yml`に反映済み)。

### 既に同居していた別プロジェクトの移行

このデプロイ作業中に判明した通り、本VPSには既に別プロジェクトが直接ポート80/443を公開して稼働していた。そのプロジェクトも上記の共通手順に沿ってTraefik配下へ移行する。手順4の再起動時に**該当プロジェクトの短時間の再起動(ダウンタイム)を伴う**点をあらかじめ利用者へ周知しておくこと。移行後は手順5の動作確認を必ず行い、旧ドメインでの応答・証明書が問題ないことを確認してから完了とする。

### 更新(renewal)について

Traefikの`certresolver`(ACME)は証明書の自動更新を組み込みで行うため、certbotのようなcronによる手動更新設定は不要。`docker compose -f /opt/traefik/docker-compose.yml logs traefik`で更新ログを確認できる。

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
docker compose -f docker-compose.prod.yml ps        # backend/postgres/redis の STATUS/HEALTH 列を確認
curl -s https://your-domain.example.com/health       # {"status":"ok","database":"ok","redis":"ok"} を期待(Traefik経由)
docker compose -f docker-compose.prod.yml logs -f backend   # アプリログの確認
docker compose -f /opt/traefik/docker-compose.yml logs -f traefik   # リバースプロキシ側のログ確認
```

`backend`は`backend/Dockerfile`にHEALTHCHECK命令を組み込み済みのため、コンテナ単体でも`docker compose ps`のSTATUS列に`(healthy)`/`(unhealthy)`が表示される(Phase 5-1で追加)。

## 8. devex-ui(Vercel)デプロイ手順

1. [Vercel](https://vercel.com)でアカウント作成後、devex-uiのGitHubリポジトリをインポートする(Next.jsは自動検出されるため`vercel.json`等の追加設定は不要)。
2. プロジェクトの環境変数に `NEXT_PUBLIC_API_URL` を設定する(値: ConoHa VPS側のAPIの公開URL、例 `https://your-domain.example.com`)。
   - **末尾に`/`を付けない**こと。付けると、リクエストURLが`https://host//api/...`(`//`二重)になり、パスを`/api/v1/auth`に限定したリフレッシュトークンCookieが送られず、F5でログインが切れる(症状: DevToolsのNetworkで`refresh`のURLが`//api`、`cookie:`ヘッダ無し、`401`)。`NEXT_PUBLIC_*`はビルド時に埋め込まれるため、修正後は**再デプロイ**が必要。devex-ui側でも`src/lib/api/base-url.ts`が末尾スラッシュを除去するが、環境変数自体も正しく設定すること。
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

このワークフローは**2回目以降の更新反映を自動化するもの**であり、初回セットアップ(1〜3節)自体は自動化しない。導入前提として、VPS上で以下が完了していること:

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

設定後は、`main`ブランチへのpush(マージ含む)のたびに、lint・testが通れば自動的にVPSへ`git pull`+イメージの再ビルド+マイグレーション適用+コンテナの入れ替えが行われる。

**順序(Phase 24 で変更)**: `build backend` → 使い捨てのコンテナ(`run --rm`)で`alembic upgrade head` → `up -d`。マイグレーションが失敗すると`set -e`でそこで止まり、古いコードのコンテナが古いスキーマのまま動き続ける。以前は`up -d --build`の後にマイグレーションを流していたため、失敗すると新しいコードが古いスキーマで動いてしまっていた。手動デプロイ(3節)は初回セットアップ時、または自動デプロイが使えない状況(SSH鍵の再発行中等)の代替手段として引き続き使える。

## 10. ステージ3・4の本番反映(Phase 24)

本番がステージ2(`main` = `a7176af`)のときに、ステージ3(UML設計図)・ステージ4(詳細設計モード)をまとめて反映する手順。マイグレーションが6本走る(ステージ3の`f1a2b3c4d5e6`・`b7c8d9e0f1a2`と、ステージ4の`c1d2e3f4a5b6`〜`f4a5b6c7d8e9`の4本)。新しい環境変数は無い(`REFRESH_TOKEN_EXPIRE_DAYS`の既定値が30→14日になっただけ。`.env`で指定していれば、その値のまま)。

**API(devex-api)を先に、UI(devex-ui)を後に出す**。UI を先に出すと、モード選択・詳細設計画面が古い API を呼んで失敗する。

1. **バックアップ**(VPS。5節): `pg_dump`でダンプを取り、VPS の外へコピーする。
2. **重複の確認**(VPS): `e3f4a5b6c7d8`は、generated_documents に同じ版の番号の行があると止まる(どの行を残すかは人が決めるため、自動では消さない)。次の SQL が0行であることを確かめる。

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
     SELECT project_id, doc_type, version, COUNT(*) FROM generated_documents
     GROUP BY project_id, doc_type, version HAVING COUNT(*) > 1;"'
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"'
   # → a96a8c02c148 (ステージ2の head)であることも確かめる
   ```

3. **devex-api の反映**(手元): `git fetch`で origin/main を取り込み、`stage4`との差を確かめてから、`stage4`→`main`の PR を作ってマージする。CI の test が通ると、9節の自動デプロイが走る。
4. **確認**: GitHub Actions のログで、マイグレーション6本が`Running upgrade`と出ていることを確かめる。`curl -s https://<ドメイン>/health`が`{"status":"ok",...}`を返すことを確かめる。
5. **devex-ui の反映**(手元): 4 が済んでから`main`を push する(Vercel が自動でデプロイする)。
6. **本番での確認**: 実際の Gemini で、次の2つを通す。
   - 簡易ドキュメントモード: ログイン → 作成 → ヒアリング → 4文書。
   - 詳細設計モード: 段階1〜7 → zip のダウンロード。

   図の自動レイアウトにかかった時間と、段階7の生成にかかった時間を控えておく。

**ロールバック**(6節): コードは`main`を`git revert`して push する。マイグレーションは`alembic downgrade a96a8c02c148`で戻す(ステージ3・4で作ったテーブル・列を消す。ローカルの本番相当の構成で、6本の往復を確かめた)。詳細設計モードのデータを残す必要があるなら、downgrade ではなく、1 のバックアップから戻すことを考える。

