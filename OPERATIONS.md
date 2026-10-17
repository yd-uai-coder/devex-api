# 運用マニュアル(デプロイ・バックアップ・監視)

このドキュメントは、`devex-api`(FastAPI + PostgreSQL + Redis)をConoHa VPS(生VPS。マネージドPaaSではなく自前でDocker運用)へ、`devex-ui`(Next.js)をVercelへデプロイ・運用するための手順書。個人開発規模を想定しており、エンタープライズ級の監視基盤等は求めない。

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
  - `GOOGLE_API_KEY` ── 実際のGemini APIキー。`GEMINI_MODEL`は`gemini-3.5-flash-lite`を使うこと(`gemini-2.5-flash-lite`は新規利用不可、404 NOT_FOUND)。
  - `CORS_ORIGINS` ── **Vercelの本番URL(例: `https://devex-ui.vercel.app`、カスタムドメインを設定した場合はそちらも)を含める**。未設定時のデフォルトは`["http://localhost:3000"]`のみで、Vercel上のdevex-uiからのAPI呼び出しがすべてCORSエラーになるため、初回デプロイ前に必ず設定する。
  - `ENCRYPTION_KEY` ── マイページで登録する API キーを暗号化する Fernet の鍵。本番の backend のコンテナで作り、`.env`とパスワード管理ツールにだけ置く(作り方・回し方は 10.6節)。
  - `E2E_FAKE_LLM` ── `.env`に**書かない、または`false`のままにする**(起動時バリデーションが`ENVIRONMENT=production`かつ`E2E_FAKE_LLM=true`を拒否する)。

## 3. 初回デプロイ手順(devex-api)

```bash
cd devex-api
# .env を用意済みであることを前提とする(上記2.参照)
# edge ネットワーク(1節でTraefikが作成済み)が無いと backend の起動に失敗する点に注意
# マイグレーションを、backend の起動より前に流す(9節の自動デプロイと同じ順)
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
# $POSTGRES_USER・$POSTGRES_DB はコンテナの中にだけある変数なので、sh -c '...' で囲んで
# コンテナの中で展開する(VPS のシェルで展開すると空になり、root で接続して失敗する)。
docker compose -f docker-compose.prod.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > backup_$(date +%F).sql
# pg_dump が失敗しても、リダイレクトで空のファイルができる。空でないことを確かめる
test -s backup_$(date +%F).sql && head -5 backup_$(date +%F).sql
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

`backend`は`backend/Dockerfile`にHEALTHCHECK命令を組み込み済みのため、コンテナ単体でも`docker compose ps`のSTATUS列に`(healthy)`/`(unhealthy)`が表示される。

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

**順序**: `build backend` → 使い捨てのコンテナ(`run --rm`)で`alembic upgrade head` → `up -d`。マイグレーションが失敗すると`set -e`でそこで止まり、古いコードのコンテナが古いスキーマのまま動き続ける(新しいコードが古いスキーマで動く状態を作らないため)。手動デプロイ(3節)は初回セットアップ時、または自動デプロイが使えない状況(SSH鍵の再発行中等)の代替手段として引き続き使える。

## 10. 詳細設計モードを含む版への更新

本番が`a7176af`(詳細設計モードを含まない版)のときに、UML図・詳細設計モードを含む版へ更新する手順。マイグレーションが6本走る(`f1a2b3c4d5e6`・`b7c8d9e0f1a2`・`c1d2e3f4a5b6`〜`f4a5b6c7d8e9`)。新しい環境変数は無い(`REFRESH_TOKEN_EXPIRE_DAYS`の既定値が30→14日になっただけ。`.env`で指定していれば、その値のまま)。

**API(devex-api)を先に、UI(devex-ui)を後に出す**。UI を先に出すと、モード選択・詳細設計画面が古い API を呼んで失敗する。

1. **バックアップ**(VPS。5節): `pg_dump`でダンプを取り、VPS の外へコピーする。
2. **重複の確認**(VPS): `e3f4a5b6c7d8`は、generated_documents に同じ版の番号の行があると止まる(どの行を残すかは人が決めるため、自動では消さない)。次の SQL が0行であることを確かめる。

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
     SELECT project_id, doc_type, version, COUNT(*) FROM generated_documents
     GROUP BY project_id, doc_type, version HAVING COUNT(*) > 1;"'
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"'
   # → a96a8c02c148 (更新前の head)であることも確かめる
   ```

3. **devex-api の反映**(手元): `git fetch`で origin/main を取り込み、`stage4`との差を確かめてから、`stage4`→`main`の PR を作ってマージする。CI の test が通ると、9節の自動デプロイが走る。
4. **確認**: GitHub Actions のログで、マイグレーション6本が`Running upgrade`と出ていることを確かめる。`curl -s https://<ドメイン>/health`が`{"status":"ok",...}`を返すことを確かめる。
5. **devex-ui の反映**(手元): 4 が済んでから`main`を push する(Vercel が自動でデプロイする)。
6. **本番での確認**: 実際の Gemini で、次の2つを通す。
   - 簡易ドキュメントモード: ログイン → 作成 → ヒアリング → 4文書。
   - 詳細設計モード: 段階1〜7 → zip のダウンロード。

   図の自動レイアウトにかかった時間と、段階7の生成にかかった時間を控えておく。

**ロールバック**(6節): コードは`main`を`git revert`して push する。マイグレーションは`alembic downgrade a96a8c02c148`で戻す(この更新で作ったテーブル・列を消す。ローカルの本番相当の構成で、6本の往復を確かめた)。詳細設計モードのデータを残す必要があるなら、downgrade ではなく、1 のバックアップから戻すことを考える。

### 10.1 プロジェクト名の必須化・ヒアリング完了判定の保存を含む版への更新

10節と同じく、API を先に、UI を後に出す。マイグレーションは`a5b6c7d8e9f0`(`projects.hearing_check`の追加)の1本で、9節の自動デプロイが流す。

- **開いていた画面は再読み込みが要る**: この版の API は、プロジェクトの作成に`name`(プロジェクト名)を必須にする。API を出してから新しい UI が出るまでの間と、それより前に開いていたタブでは、作成フォームにプロジェクト名の欄が無く、送信すると 422(画面の表示は「Field required」)になる。UI の反映後に、ブラウザで再読み込み(Shift+F5)すれば欄が出る。
- **ロールバック**: マイグレーションは`alembic downgrade f4a5b6c7d8e9`で戻す(列を消すだけ)。


### 10.2 実装手順書(段階8・簡易モードの手順書)を含む版への更新

本番が 10.1 の版(alembic の head が`a5b6c7d8e9f0`)のときに、実装手順書を含む版(`stage5`)へ更新する手順。10節と同じく、**API を先に、UI を後に出す**(UI を先に出すと、段階8・「実装手順書へ進む →」が古い API を呼んで失敗する)。新しい環境変数・依存の追加は無い。

流れるマイグレーションは2本で、9節の自動デプロイが流す。

| リビジョン | 内容 | 利用者への影響 |
|---|---|---|
| `b8c9d0e1f2a3` | 段階7(実装計画)のタスクを作業単位の形(機能/基盤・依存・モジュール/環境・設定のファイル)に変換する。データだけの移行で、列は変えない | 承認済みの段階7は「レビュー中」に戻る。依存は空になるので、開き直して確かめ(必要なら再生成し)、承認し直す |
| `c9d0e1f2a3b4` | `design_stages`の CHECK を段階1〜8に広げる | なし |

簡易ドキュメントモードの既存のプロジェクトは、マイグレーションの対象外。この版より前に生成した実装計画書(番号の無い WBS)・内部設計書(モジュール一覧の無いもの)では、段階8を開くと`WBS_MISSING`・`NO_MODULE_LIST`の指摘が出て単位が空になる。文書を再生成すれば使える。

1. **バックアップ**(VPS。5節): `pg_dump`でダンプを取り、VPS の外へコピーする。
2. **更新前の head の確認**(VPS):

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"'
   # → a5b6c7d8e9f0 であることを確かめる
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
     SELECT status, COUNT(*) FROM design_stages WHERE stage = 7 GROUP BY status;"'
   # → 承認し直しになる段階7の件数(approved の数)を控えておく
   ```

3. **devex-api の反映**(手元): `git fetch`で origin/main を取り込み、`stage5`との差を確かめてから、`stage5`→`main`の PR を作ってマージする。CI の test が通ると、9節の自動デプロイが走る。
4. **確認**: GitHub Actions のログで、マイグレーション2本が`Running upgrade`と出ていることを確かめる。`curl -s https://<ドメイン>/health`が`{"status":"ok",...}`を返すことを確かめる。2 の SQL をもう一度流し、段階7に approved が無いこと(すべて reviewing などに戻っていること)を確かめる。
5. **devex-ui の反映**(手元): 4 が済んでから`main`を push する(Vercel が自動でデプロイする)。開いていた画面は再読み込み(Shift+F5)する。
6. **本番での確認**(実際の Gemini で): 次の表の項目を確かめ、結果を記録しておく。時間は生成ボタンを押してから「生成済」になるまで。

   | 対象 | 確かめること |
   |---|---|
   | 詳細設計モード(既存のプロジェクト) | 段階7がレビュー中に戻り、作業単位(機能/基盤・依存)が表示される。承認し直せる |
   | 詳細設計モード(段階1〜8) | 段階8で単位を選んで生成する(時間を控える)。手順書の中身・指摘の重要度と直す先・`UNRESOLVED_CALL`の文言とボタン |
   | 〃 | 単位の詳細の展開・編集、段階8の単位の図と`STUB_OUTSIDE_SEQUENCE`の出方、最重要の指摘が残るときの承認前の確認 |
   | 〃 | 段階5の下書きの種別(呼び出し/非同期/戻り)の付き方、段階5のタブの図と指摘 |
   | 〃 | 2つのダウンロードのボタンが承認まで押せない。詳細設計書の zip の 05章(HTML の図・md の Mermaid)、実装手順書の zip の中身と見た目 |
   | 〃 | 「AI 向けにコピー」の警告と、写した md を AI Coding Agent に渡したときの使い勝手 |
   | 簡易ドキュメントモード | 4文書を生成し、実装計画書の WBS が書式どおりか(`WBS_FORMAT`・`WBS_ID_MISMATCH`の数) |
   | 〃 | SCR-005 の「実装手順書へ進む →」、段階8だけの SCR-008(見出し・帯・単位の一覧・段階1〜7の押せない行と注記) |
   | 〃 | 手順書の生成(未定義の多さと「詳細設計モードで詰める」の勧め、DF の無い単位にデータモデルが添うこと、モジュールが層で展開されること)、「〇〇書を直す(再生成)」、実装手順書の zip |

**ロールバック**(6節): コードは`main`を`git revert`して push する。マイグレーションは`alembic downgrade a5b6c7d8e9f0`で戻す。

- `c9d0e1f2a3b4`の downgrade は、**段階8の行(手順書)をすべて消す**(CHECK を段階1〜7に戻すため)。
- `b8c9d0e1f2a3`の downgrade は、段階7のタスクの形だけを戻し、承認は戻さない(レビュー中のまま)。
- 開発環境の DB の複製で、2本の downgrade → upgrade → downgrade → upgrade を往復して確かめた。
- 手順書を残す必要があるなら、downgrade ではなく、1 のバックアップから戻すことを考える。

### 10.3 ヒアリング改良・上流の構造化(観点の表・選択肢カード・要件定義書と外部設計書の model)を含む版への更新

本番が 10.2 の版(alembic の head が`c9d0e1f2a3b4`)のときに、ヒアリング改良と上流の構造化を含む版(ステージ6)へ更新する手順。10節と同じく、**API を先に、UI を後に出す**(UI を先に出すと、観点の表・カードの送信・要件の「採用にする」が古い API を呼んで失敗する)。新しい環境変数・依存の追加は無い。

流れるマイグレーションは3本で、9節の自動デプロイが流す。どれもテーブル・列を足すだけで、既存の行は書き換えない。

| リビジョン | 内容 | 利用者への影響 |
|---|---|---|
| `a1b2c3d4e5f7` | `hearing_points`(観点の表)を作り、`projects.hearing_scale`(規模)を足す | 既存のプロジェクトは、観点の表の行を持たない(完了判定を保存済みなら、画面は「以前の判定(5条件)で進んでいます」と出す)。次の発言から観点の表で判定する |
| `b2c3d4e5f6a8` | `chat_histories.cards`(選択肢カード)を足す | 既存の会話にカードは無い。次の返信から付く(規模が未決なら、まず規模のカード) |
| `c3d4e5f6a7b9` | `generated_documents.model`(要件定義書・外部設計書の意味モデル)を足す | 既存の文書は model を持たない。プレビューのパネルは「以前の形式」と出し、要件ID・出どころ・要件の追跡(段階1の要件の欄・簡易モードの WBS の「要件」欄・手順書の「要件との対応」)は空になる。文書を再生成すると使える |

既存のデータについて:

- **会話の順**: この版より前の会話は、同じ送信の発言と返信が同じ時刻のまま残る(並びが入れ替わって見えることがある)。直さない(移行の処理は作っていない)。
- **実装計画書の見出し**: 以前の版で生成した実装計画書に、見出しの記号の重なり(`### ### 1.`)が残っていることがある。再生成すると直る。

1. **バックアップ**(VPS。5節): `pg_dump`でダンプを取り、VPS の外へコピーする。
2. **更新前の head の確認**(VPS):

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"'
   # → c9d0e1f2a3b4 であることを確かめる
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
     SELECT status, COUNT(*) FROM projects GROUP BY status;"'
   # → ヒアリング中(interviewing)のプロジェクトの数を控えておく(観点の表の行を持たない)
   ```

3. **devex-api の反映**(手元): ステージ6のコミットは、手元の`stage5`ブランチの上に積んである。`git fetch`で origin を取り込み、`stage6`ブランチを作って push する(`git switch -c stage6`・`git push -u origin stage6`)。`stage6`と origin/main の差を確かめてから、`stage6`→`main`の PR を作ってマージする。CI の test が通ると、9節の自動デプロイが走る。
4. **確認**: GitHub Actions のログで、マイグレーション3本が`Running upgrade`と出ていることを確かめる。`curl -s https://<ドメイン>/health`が`{"status":"ok",...}`を返すことを確かめる。2 の 1つ目の SQL が`c3d4e5f6a7b9`を返すことを確かめる。
5. **devex-ui の反映**(手元): 4 が済んでから`main`を push する(Vercel が自動でデプロイする)。開いていた画面は再読み込み(Shift+F5)する。
6. **本番での確認**(実際の Gemini で): 次の表の項目を確かめ、結果を記録しておく。

   | 対象 | 確かめること |
   |---|---|
   | ヒアリング(既存のプロジェクト) | ヒアリング中のプロジェクトで観点の表が出て(完了判定を保存済みなら「以前の判定」の表示)、次の発言から表が埋まる |
   | ヒアリング(新しいプロジェクト、両モード) | 最初の発話に規模のカードが付く。開発環境のフォームを埋めたとき、技術スタックのカードのおすすめにフォームの値が出るか |
   | 〃 | 観点の表(広い画面で右・狭い画面で上に畳む・広い画面での sticky)、実際の LLM での表の埋まり方、必須がそろったときのまとめの読みやすさ |
   | 〃 | カードの見た目、カードごとの「この回答を送る」と下の送信ボタンの使い分け、答えなかったカードの付け直し、狭い画面でのカード、送信した発言の改行、返信がまとめて表示される体感 |
   | 〃 | 未決の必須を表から「対象外」にする操作、必須がそろった後の「任意項目についてヒアリングを続ける」と任意の観点の問いの質 |
   | 要件定義書(両モード) | タブの要約のパネル(出どころの件数と注釈・指摘・推測の要件と「採用にする」)、md の表の読みやすさ(1.4〜1.6 と付録)、狭い画面でのパネル。規模の制約(CON)が「推測」にならないこと |
   | 〃 | 「採用にする」を取り消せないことが困らないか(取り消しは作っていない) |
   | 外部設計書(両モード) | タブのパネル(件数と指摘)、2.6 の表(8列。狭い画面での横の広がり)、本文の要らない POST が「なし」になり`API_BODY_MISSING`が出ないこと、2.3 の UI 仕様の小見出し、付録「要件との対応」 |
   | 詳細設計モード | 段階1の表の「要件」の欄と関連画面の「, 」区切り、段階1を再生成したときに人が直した要件が下書きに戻ること(困らないか)、段階8の単位の一覧の「要件」列 |
   | 簡易ドキュメントモード | 実装計画書の WBS の「要件」欄、実装手順書の index の「4. 要件との対応」と「非機能要件・制約」の表、見出しの記号が重ならないこと |

**ロールバック**(6節): コードは`main`を`git revert`して push する。マイグレーションは`alembic downgrade c9d0e1f2a3b4`で戻す。

- 3本の downgrade は、足したテーブル・列を消す。**観点の表・規模・カード(選んだ回答を含む)・文書の model(要件ID・出どころ・採用にした記録)がすべて消える**。会話の本文と文書の md は残る(カードへの回答は、発言の本文の「【観点】選択肢」として残る)。
- 開発環境の DB の複製で、3本の downgrade → upgrade → downgrade → upgrade を往復して確かめた(upgrade の後、消えたデータは戻らず空のまま)。
- ステージ6の期間に進めたヒアリングや生成した文書の model を残す必要があるなら、downgrade ではなく、1 のバックアップから戻すことを考える。

### 10.4 変更影響分析・変更管理(要素の印・承認時の写し・簡易モードの文書の model)を含む版への更新

本番が 10.3 の版(alembic の head が`c3d4e5f6a7b9`)のときに、変更影響分析・変更管理を含む版(ステージ7)へ更新する手順。10節と同じく、**API を先に、UI を後に出す**(UI を先に出すと、「変更の影響」パネル・印の確認済み・簡易モードの文書の要約のパネルが古い API を呼んで失敗する)。新しい環境変数・依存の追加は無い(ステージ6の版から`config.py`・`pyproject.toml`・`uv.lock`・compose・CI・nginx・Dockerfile の差が無い)。

流れるマイグレーションは2本で、9節の自動デプロイが流す。

| リビジョン | 内容 | 利用者への影響 |
|---|---|---|
| `d4e5f6a7b8c0` | 段階7のタスクに単位のキー(`U-001`…)を並び順で振り、段階8の手順書に同じプロジェクトの段階7から引いたキー(`unit_key`)を書く。データだけの移行で、列・状態・版は変えない | なし(承認済みのまま)。段階7に無い単位 ID の手順書はキーを持たない(検証の`UNIT_MISMATCH`のまま)。簡易モードのプロジェクトは変えない |
| `e5f6a7b8c9d1` | `design_stages`に`approved_model`(直前の承認の写し)・`impact`(変更の影響の印)を足す。承認済みで承認の後に変わっていない行にだけ、今の内容を写しとして入れる | 承認の後に編集していた段階は写しを持たず、次に承認するまで差分を取らない(今までどおり段階の「古い」だけで扱う)。印は空で始まる(更新より前の変更の影響は出ない) |

既存のデータについて:

- **簡易ドキュメントモードの文書**: この版より前に生成した内部設計書・実装計画書は model を持たない。プレビューの要約のパネルは「以前の形式」と出し、段階8は今までどおり md を解析して読む。文書を再生成すると model ができる。**model の無い版から再生成した最初の1回は印が付かず**、差分の再生成(変えない行を前の版のまま使う)も効かない(全部を書き直す)。
- **簡易モードの以前の手順書**: 段階8の手順書は単位の ID(`M-01-T01`)のまま。再生成した実装計画書の単位のキー(`U-001`…)では引けないので、文書を変えても以前の手順書には印が付かない。手順書を生成し直すと付く。
- **ヒアリング**: すでにヒアリングしたプロジェクトでも、新しい観点(データの保全・保持と削除・ログ・不正利用)は未決として観点の表に出る。必須の観点は、チャットで答えると埋まる。生成済みの版の検証は、その版の観点だけを数える(新しい観点で指摘は増えない)。
- **段階3・5**: 既存の DFD・CRUD 図・手順は直さない。再生成すると新しい規則(DFD の書き込みの線・CRUD の補い・分岐の入れ子)で作られる。

1. **バックアップ**(VPS。5節): `pg_dump`でダンプを取り、VPS の外へコピーする。
2. **更新前の head の確認**(VPS):

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"'
   # → c3d4e5f6a7b9 であることを確かめる
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
     SELECT stage, status, COUNT(*), COUNT(*) FILTER (WHERE version = approved_version) AS unchanged
     FROM design_stages GROUP BY stage, status ORDER BY stage, status;"'
   # → 段階ごとの件数を控えておく(approved かつ unchanged の行が写しを持つ)
   ```

3. **devex-api の反映**(手元): ステージ7のコミットは、手元の`stage7`ブランチに積んである(`stage6`の上)。`git fetch`で origin を取り込み、`stage7`を push する(`git push -u origin stage7`)。`stage7`と origin/main の差を確かめてから、`stage7`→`main`の PR を作ってマージする。CI の test が通ると、9節の自動デプロイが走る。
4. **確認**: GitHub Actions のログで、マイグレーション2本が`Running upgrade`と出ていることを確かめる。`curl -s https://<ドメイン>/health`が`{"status":"ok",...}`を返すことを確かめる。2 の1つ目の SQL が`e5f6a7b8c9d1`を返し、次の SQL で写しの件数が 2 の unchanged の承認済みの数と同じであることを確かめる。

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
     SELECT COUNT(approved_model) AS snapshots, COUNT(impact) AS impacts FROM design_stages;"'
   # → impacts は 0
   ```

5. **devex-ui の反映**(手元): 4 が済んでから`main`を push する(Vercel が自動でデプロイする)。開いていた画面は再読み込み(Shift+F5)する。メニューの「開発用」は無くなる(デモのページ`/change-impact-demo`は URL を直接開けば見られる)。
6. **本番での確認**(実際の Gemini で): 次の表の項目を確かめ、結果を記録しておく。

   | 対象 | 確かめること |
   |---|---|
   | 詳細設計モード(既存のプロジェクト) | 承認済みの段階が承認済みのまま。段階7の単位・段階8の手順書がそのまま開ける。印は出ない |
   | 詳細設計モード(段階1〜8 を承認した後) | 段階1の処理を1つ直して保存すると、「変更の影響」パネルに「承認すると届く」印が出る。承認し直すと下流の段階に印が付き、ステッパーに「見直し N」、各段階の表の行に印が出る |
   | 〃 | パネルの操作感(作業領域の上・全幅、段階に追従する絞り込み・「全段階」・「未確認のみ」、たどった道と理由の読みやすさ)。直した段階を選んでいるときはパネルが「この段階に届いた印はありません」と出て、変わったものは「全段階」で見える ── 困らないか |
   | 〃 | 印の確認済み・未確認に戻す、「表で見る」の強調、「段階Nで直す」の移動先の強調(名前の照合の当たり)。未確認の印が残る段階は「承認する」で理由が出て、確認済みにすると承認し直せる |
   | 〃 | 再生成の候補から段階5・6・8 を部分再生成できる。要件定義書・外部設計書を再生成したとき、変えていない行が前の版のまま残り、頼んでいない変更で印が増えない |
   | 〃 | 影響の取得の重さ(段階の一覧のたびに取る。生成のポーリング中に重く感じないか) |
   | 簡易ドキュメントモード | 4文書の生成。内部設計書・実装計画書のタブの要約のパネル(件数と指摘)、実装計画書の単位のキー |
   | 〃 | 段階8の手順書を承認した後に文書を再生成すると、変わった要件・処理(DF)を担う単位にだけ印が付き、「変更の影響」パネルが出る。確認済みにすると承認し直せる |
   | ヒアリング(新しいプロジェクト、両モード) | 観点の表に側面の4観点(データの保全・保持と削除・ログ・不正利用)が大項目のラベルの下に並び、規模ごとに任意になる。発言の数(詳細 18〜19・簡易 16〜17 程度)が長すぎないか。ログのカードのおすすめが利用者の答えと逆を向かないか |
   | 詳細設計モード(段階3・5) | 段階3の補ったセル(`DFD_WRITE_INFERRED`の警告)と、段階5の分岐の欄(入れ子と参照) |

**ロールバック**(6節): コードは`main`を`git revert`して push する。マイグレーションは`alembic downgrade c3d4e5f6a7b9`で戻す。

- `e5f6a7b8c9d1`の downgrade は、写しと印の列を消す。**付いていた印と確認済みの記録、承認時の写しがすべて消える**。段階の内容・状態・版は残る。
- `d4e5f6a7b8c0`の downgrade は、段階7のタスクの単位のキーと、段階8の手順書の`unit_key`を外す(内容の意味は変わらない)。
- 開発環境の DB の複製(段階 175行・写し 154・印 45)で、2本の downgrade → upgrade → downgrade → upgrade を往復して確かめた。upgrade の後、写しは承認済みで変わっていない行(153)だけに戻り、印は 0 になる。詳細設計モードの手順書のキーはすべて戻るが、簡易モードの手順書に付いていたキー(5行)は戻らない(この移行は簡易モードを変えないため)。
- 印や確認済みの記録を残す必要があるなら、downgrade ではなく、1 のバックアップから戻すことを考える。

### 10.5 AI 設計レビュー・テスト仕様書(レビューの区切り・差し戻し・「直ったか」・手順書の検証・テスト仕様書)を含む版への更新

本番が 10.4 の版(alembic の head が`e5f6a7b8c9d1`)のときに、AI 設計レビューとテスト設計を含む版(ステージ8)へ更新する手順。10節と同じく、**API を先に、UI を後に出す**(UI を先に出すと、レビューのパネル・テスト仕様書のタブ・再生成の起点の選択が古い API を呼んで失敗する)。

この版で増えるもの:

- **環境変数**: `ANTHROPIC_API_KEY`(設計レビューの Claude Sonnet と、「直ったか」の判定の Claude Haiku が使う)。任意で`REVIEW_MODEL`(既定`claude-sonnet-5-5`)・`JUDGE_MODEL`(既定`claude-haiku-5-5`)・`REVIEW_RATE_LIMIT_PER_DAY`(superuser ごとの1日のレビューの回数。既定20)・`JUDGE_RATE_LIMIT_PER_DAY`(既定200)。キーが無いと、レビューは失敗の回として残り、今までどおりの自己診断に切り替わる(生成は止まらない)。
- **依存**: `langchain-anthropic`(`pyproject.toml`・`uv.lock`)。これに伴い`langchain-core`が 1.5.4 → 1.6.10 に上がる(生成・ヒアリングの Gemini の呼び出しも同じ`langchain-core`を通る。全体テストと E2E で確かめた)。9節の自動デプロイがイメージを作り直すので、手で入れる作業は無い。
- **マイグレーション**: 5本で、どれもテーブル・列を足すだけ(既存の行は書き換えない)。9節の自動デプロイが流す。

| リビジョン | 内容 | 利用者への影響 |
|---|---|---|
| `f6a7b8c9d0e1` | `review_runs`(レビューの回)・`review_findings`(指摘と採否)を作り、`users.review_enabled`(レビューのオン・オフ。既定オフ)を足す | なし。レビューは superuser がオンにしたときだけ流れる(実効値 = `is_superuser` かつ`review_enabled`) |
| `a7b8c9d0e1f2` | `review_findings.documents`(指摘に関わる文書)を足す | なし |
| `5a1c7e93d2b4` | `review_runs.snapshots`(段階の区切りのレビューの、段階の写し)を足す | なし |
| `6b2d8fa4e3c5` | `hearing_points.reopened_after`(聞き直しで観点を未決に戻した発言)を足す | なし |
| `7c3e9ab5f4d6` | `review_fix_checks`(採用した指摘が直ったかの判定)を作る | なし |

既存のデータについて:

- **テスト仕様書**: 移行は作らない(`generated_documents`の新しい`doc_type="test_spec"`)。簡易モードの既存のプロジェクトは、4文書を再生成すると一緒に作られる(タブは「まだテスト仕様書がありません」)。詳細設計モードは、段階1・7 を承認していれば詳細設計画面の段階8の下の行から作れる。
- **実装手順書の検証**: 以前に生成した手順書にも、新しい検証(`DEPENDENCY_BUILT_LATER`・`NO_TEST_FILE`・`NO_SCHEMA_UNIT`・`TABLE_WITHOUT_UNIT`・`NO_ERROR_TEST`)の指摘が出ることがある(表示のときに検証するため)。手順書の中身は変わらない。
- **レビュー**: 既存のプロジェクトの区切りは「未レビュー」で始まる(自動では流さない)。レビューをオンにした後、パネルの「レビューする」で流せる。

1. **バックアップ**(VPS。5節): `pg_dump`でダンプを取り、VPS の外へコピーする。
2. **更新前の head の確認**(VPS):

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"'
   # → e5f6a7b8c9d1 であることを確かめる
   ```

3. **鍵の設定**(VPS): devex-api の`.env`に`ANTHROPIC_API_KEY=<鍵>`を足す(必要なら上の任意の変数も)。4 の自動デプロイの`up -d`がコンテナを作り直すときに読み込まれる。鍵は Anthropic Console で、このサービス専用に作る(使用量の上限を Console 側にも設定しておくと、費用の歯止めが二重になる)。
4. **devex-api の反映**(手元): ステージ8のコミット(Phase 51〜60)は、手元の`stage7`ブランチの上に積んである。`git fetch`で origin を取り込み、`stage8`ブランチを作って push する(`git switch -c stage8`・`git push -u origin stage8`)。`stage8`と origin/main の差を確かめてから、`stage8`→`main`の PR を作ってマージする。CI の test が通ると、9節の自動デプロイが走る。
5. **確認**: GitHub Actions のログで、マイグレーション5本が`Running upgrade`と出ていることを確かめる。`curl -s https://<ドメイン>/health`が`{"status":"ok",...}`を返すことを確かめる。2 の SQL が`7c3e9ab5f4d6`を返すことを確かめ、鍵がコンテナに入ったかを値を出さずに確かめる。

   ```bash
   docker compose -f docker-compose.prod.yml exec -T backend sh -c 'test -n "$ANTHROPIC_API_KEY" && echo set || echo missing'
   # → set
   ```

6. **superuser の昇格**(VPS): レビューを使うアカウントを、画面から登録してから手で昇格させる(seed は dev 専用で、本番では流さない)。

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
     UPDATE users SET is_superuser = true WHERE email = '\''<メールアドレス>'\'' RETURNING email, is_superuser, review_enabled;"'
   # → 1行。review_enabled は false(画面のパネルでオンにする)
   ```

7. **devex-ui の反映**(手元): 5 が済んでから`main`を push する(Vercel が自動でデプロイする)。開いていた画面は再読み込み(Shift+F5)する。メニューの「開発用」は無くなる(デモのページ`/design-review-demo`は URL を直接開けば見られる)。
8. **本番での確認**(実際の Gemini・Claude で): 次の表の項目を確かめ、結果と Anthropic の費用(Console の使用量)を記録しておく。Claude を流すのは表の「費用」の欄の分だけにする(見込み 約 $0.6・最大 約 $1.2。単価は Sonnet 5.5 入力 $2・出力 $10 / 100万トークン、Haiku 5.5 入力 $0.10・出力 $0.50)。

   | 対象 | 確かめること | 費用 |
   |---|---|---|
   | レビューのオン・オフ | 一般の利用者にはパネルが出ない。superuser はパネルのスイッチでオンにでき、オフのときは生成の後に今の自己診断が出る | なし |
   | 簡易モード(新しいプロジェクト、オン) | 4文書の生成の後に自動のレビューが流れ、チャットに件数の一行、パネルに指摘(重要度の順・文書の凡例 A〜D・対象へ飛ぶ)。採用・却下・保留、「採用 n(未対応 m)」、各タブの表のバッジ | Sonnet 1回 約 $0.10 |
   | 〃(聞き直しの通し。Phase 59 の申し送り) | ヒアリングの指摘を採用 →「チャットで聞き直す」→ 観点が未決に戻り、値の選択肢カードで答える →「おすすめでよい」と答えても値が決まる → 要件定義書から再生成 → 自動のレビューが流れ直し、前の回で採用した指摘のチップ(「変更あり」・「AI: 直った」)が出る | Sonnet 1回 約 $0.10 + Haiku 数件 |
   | 〃(差し戻し) | 設計の指摘を採用 → 再生成のダイアログで、採用した指摘が選ばれた状態・起点の初期値が関連する最も上流の文書になる(「AI 設計レビューの採用した指摘を、再生成の指示に入れる」)→ 作り直した版で指摘の対象が直っている | (上の流れ直しに含む) |
   | 〃(テスト仕様書) | 5つ目のタブ「テスト仕様書(結合・総合)」・作り直しと進み具合・検証の結果の表と絞り込み・版の履歴の復元・zip の`test_spec.md`と注記 | なし(Gemini) |
   | 〃(実装手順書) | 手順書の「未定義・要決定の一覧」、参照の 2.6 の行のエラー欄、検証の結果に`NO_ERROR_TEST`などが出たときの読みやすさ | なし |
   | 詳細設計モード(新しいプロジェクト、オン) | 要件・外部の生成の後の自動のレビュー。段階6・7 の承認のダイアログから「レビューする」、区切りの状態(未レビュー・レビュー中・レビュー済み)、指摘から段階の対象へ飛ぶ(推定の当たり)、「採用した指摘で段階を作り直す」、各段階のバッジの強調、承認の知らせ、手順書の生成のときの未レビューの知らせ | Sonnet 3回 約 $0.38 |
   | 〃(テスト仕様書) | 段階8の下の行・作るボタンと理由(段階1・7 が未承認のとき)・段階7 のダイアログ | なし(Gemini) |
   | 〃 | レビューの出力が上限(`REVIEW_MAX_TOKENS`=16000)に当たらないか(当たるとレビューが失敗の回になる)。パネルの長さ | なし |
   | 費用 | Anthropic Console の使用量が見込み(最大 約 $1.2)に収まったか | ── |

**ロールバック**(6節): コードは`main`を`git revert`して push する。マイグレーションは`alembic downgrade e5f6a7b8c9d1`で戻す。

- downgrade は、レビューの回・指摘・採否・「直ったか」の判定のテーブルと、`users.review_enabled`・`hearing_points.reopened_after`の列を消す。**レビューの記録はすべて消える**。superuser の昇格(`is_superuser`)は残る。
- テスト仕様書の行(`doc_type="test_spec"`)は残るが、以前の版のコードは4種の文書しか読まないので、画面にも zip にも出ない(もう一度更新すると、また見える)。
- 開発環境の DB の複製(利用者 70・テスト仕様書の版 34・レビューの回 28・指摘 314・判定 74)で、5本の downgrade → upgrade を2往復して確かめた。どちらの往復でも、upgrade の後はレビューの記録が 0・`review_enabled`はすべてオフ、利用者・観点の表・テスト仕様書の行の数は変わらない。
- レビューの記録を残す必要があるなら、downgrade ではなく、1 のバックアップから戻すことを考える。

### 10.6 使い勝手・マイページ(削除・検索・アカウント・モデルと API キー・生成の失敗の知らせ)を含む版への更新

本番が 10.5 の版(alembic の head が`7c3e9ab5f4d6`)のときに、使い勝手とマイページを含む版(ステージ9)へ更新する手順。10節と同じく、**API を先に、UI を後に出す**(UI を先に出すと、一覧の`{items, total}`・マイページ・削除が古い API を呼んで失敗する。逆に API だけ先に出た間は、古い UI の一覧が新しいレスポンスの形を読めずに空になる。間を短くする)。

この版で増えるもの:

- **環境変数**: `ENCRYPTION_KEY`(マイページで登録する API キーを暗号化する Fernet の鍵。カンマ区切りで複数)。無くても起動し、キーの登録だけが 503 になる(画面は「暗号の鍵が設定されていない」と出し、既定のキーでの利用は続く)。任意で`JUDGE_MODEL`(既定`claude-haiku-5-5`)。`LLM_TIMEOUT_SECONDS`・`REVIEW_TIMEOUT_SECONDS`・`JUDGE_TIMEOUT_SECONDS`は使わなくなった(タイムアウトはモデルごとに`app/ai/llm/catalog.py`が持つ)。`.env`に残っていても無視される。
- **既定のモデル**: `.env`の`GEMINI_MODEL`(チャットと生成)・`REVIEW_MODEL`・`JUDGE_MODEL`が、そのまま全員の既定になる。利用者がマイページで選んだときだけ、その利用者の分が替わる。**本番では既定値の行(`user_model_settings`の`user_id`が NULL の行)を入れない**(seed は dev 専用)。既定を替えるときは`.env`を書き換えて`up -d`する。3つの値は選べるモデルの表(`app/ai/llm/catalog.py`)に無いと、その用途の呼び出しが失敗するので、4 の前に表と照らす。
- **依存**: devex-api に`cryptography`、devex-ui に`@tamagui/toast`(2.6.0 に固定)。どちらもイメージ・ビルドが作り直すので、手で入れる作業は無い。
- **マイグレーション**: 5本。9節の自動デプロイが流す。

| リビジョン | 内容 | 利用者への影響 |
|---|---|---|
| `8d4f1a2b6c3e` | `projects.deleted_at`(削除の印)を足す | なし |
| `4b7e2d9c1a6f` | `users.full_name`を`user_name`に改名する(列名だけを変え、値は残す) | なし(登録の欄の名前が「ユーザーネーム」になる) |
| `9e1c5a7b3d20` | `user_model_settings`(用途ごとのモデル)・`user_api_keys`(暗号化した API キー)を作る | なし(行は利用者が選んだときにできる) |
| `a7d3c9e1f5b2` | `projects.generation_failure`(直近の生成の失敗)を足す | なし |
| `b8e4d0f2a6c3` | `design_stages.generation_finding_ids`(作り直しで渡した指摘)を足す | なし |

既存のデータについて:

- **レビュー**: superuser は今までどおり既定のキーでレビューできる。一般の利用者は、マイページでレビューの用途の AI(既定は Claude)の自分のキーを登録すると、レビューのパネルが出る(自分のキーの回は1日の上限で数えない)。
- **削除**: 削除はすぐに印を付けて 204 を返し、行は後で消す(生成中のジョブの終わりにも消す)。

1. **バックアップ**(VPS。5節): `pg_dump`でダンプを取り、VPS の外へコピーする。
2. **更新前の head の確認**(VPS):

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"'
   # → 7c3e9ab5f4d6 であることを確かめる
   ```

3. **暗号の鍵の生成と設定**(VPS): 鍵は本番の backend のコンテナで作る(手元の端末やチャットに値を残さない)。

   ```bash
   docker compose -f docker-compose.prod.yml run --rm --no-deps backend \
     uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   出た値を devex-api の`.env`に`ENCRYPTION_KEY=<値>`として足し、パスワード管理ツールにも控える(VPS の外に控えが無いと、VPS を作り直したときに鍵を失う)。4 の自動デプロイの`up -d`がコンテナを作り直すときに読み込まれる。`GEMINI_MODEL`・`REVIEW_MODEL`・`JUDGE_MODEL`が表にあることも、ここで確かめる。
4. **devex-api の反映**(手元): ステージ9のコミット(Phase 61〜72)は、手元の`stage7`ブランチの上に積んである。`git fetch`で origin を取り込み、`git log --oneline origin/main..stage7`で、ステージ9のコミットだけが並ぶ(ステージ8のコミットは既に main に入っている)ことを確かめる。違っていれば、本番に出ている版を確かめてから進む。`stage9`ブランチを作って push し(`git switch -c stage9`・`git push -u origin stage9`)、`stage9`→`main`の PR を作ってマージする。CI の test が通ると、9節の自動デプロイが走る。
5. **確認**: GitHub Actions のログで、マイグレーション5本が`Running upgrade`と出ていることを確かめる。`curl -s https://<ドメイン>/health`が`{"status":"ok",...}`を返すことを確かめる。2 の SQL が`b8e4d0f2a6c3`を返すことを確かめ、鍵がコンテナに入ったかを値を出さずに確かめる。

   ```bash
   docker compose -f docker-compose.prod.yml exec -T backend sh -c 'test -n "$ENCRYPTION_KEY" && echo set || echo missing'
   # → set
   ```

6. **devex-ui の反映**(手元): 5 が済んでから、`git fetch`・`git log --oneline origin/main..main`で出すコミットを確かめ、`main`を push する(Vercel が自動でデプロイする)。開いていた画面は再読み込み(Shift+F5)する。ログイン中の利用者は、次に認証ページを開くまでログインの印の cookie を持たない(印が無くても困らない。ログインし直すか、トークンの更新で付く)。
7. **本番での確認**(実際の Gemini・Claude で): 次の表の項目を確かめ、結果と Anthropic の費用(Console の使用量)を記録しておく。Claude を流すのは表の「費用」の欄の分だけにする(見込み 約 $0.15・最大 約 $0.3。単価は Sonnet 5.5 入力 $2・出力 $10、Haiku 5.5 入力 $0.10・出力 $0.50 / 100万トークン)。一般の利用者の自分のキーの分は、そのキーの Anthropic のアカウントに付く。

   | 対象 | 確かめること | 費用 |
   |---|---|---|
   | ヘッダー・メニュー・モード選択 | タイトルがトップへのリンク。メニューの TOP / CONTENTS / Authentication / Guide と、ログインの状態での出し分け。メニューのログアウト。`/projects/new`をモードなしで開くとダイアログ | なし |
   | 認証ページの振り分け | ログイン中に`/login`・`/register`を開くと`/dashboard`へ。ログアウトの後は開ける。DevTools で、ログインの印の cookie(`devex_logged_in`)に`Secure`が付いている | なし |
   | 削除 | カードのゴミ箱・文書と詳細設計の画面の「削除」→ ダイアログ → トースト → 一覧から消える。生成中のプロジェクトも消せる。削除した URL を開いたときの表示(今は「Project … not found」) | なし |
   | 検索・絞込み・ページ送り | 検索は Enter か「検索」で確定。モード・進行状態(複数)・条件のクリア・10件ごとのページ送り・URL に条件が残る。幅 375px での見出しの折り返し | なし |
   | アカウント | ユーザーネーム・メール・パスワードの変更。パスワードを変えても、この端末のログインは続く | なし |
   | 生成の失敗の知らせ | 失敗したときの画面の上の知らせ・承認できない理由と「検証の結果へ」・「この作り直しで渡した指摘」(失敗が起きたときだけ見られる) | なし |
   | 一般の利用者の自分のキー | 新しい一般の利用者で、マイページから Claude のキーを登録(末尾4文字だけが出る)→ チャット・生成・レビュー・判定を Claude Haiku に → 簡易モード1本(ヒアリング → 4文書 → レビューのパネル → レビュー1回)。Haiku の推論のトークンの量(Console)も見る | Haiku 約 $0.05 |
   | superuser の既定 | superuser(マイページで何も選ばない)で、既定の Sonnet のレビューを1回(ユーザーごとの解決の後も、既定の経路が動く) | Sonnet 約 $0.10 |
   | 聞き直し | 上の流れで聞き直しを通すなら、値の欠けた答えの観点が未決(open)に残るかを見る | (上に含む) |
   | 決める項目 | 一覧の検索欄の幅(`maxWidth`)・一般の利用者へのレビューの案内・ダッシュボードのカードの「前回の生成に失敗」を出すか | なし |
   | 費用 | Anthropic Console の使用量が見込み(最大 約 $0.3)に収まったか | ── |

**暗号の鍵を回す**: 新しい鍵を作り(3 と同じコマンド)、`.env`の`ENCRYPTION_KEY`の**先頭に**カンマで足して(`ENCRYPTION_KEY=<新>,<旧>`)`up -d`する。新しく登録・置き換えたキーは新しい鍵で暗号化され、前からのキーは古い鍵で読める。古い鍵を外すと、古い鍵で暗号化したキーは読めなくなり、マイページに「登録し直してください」と出る(その利用者の用途は既定のモデルに従う)。全行を暗号化し直すスクリプトは無いので、古い鍵は外さずに残しておく。**鍵を失ったとき**も同じで、新しい鍵を設定すれば、利用者がキーを登録し直すまで既定のモデルで動く。

**ロールバック**(6節): コードは`main`を`git revert`して push する(devex-ui も同じ)。マイグレーションは`alembic downgrade 7c3e9ab5f4d6`で戻す。

- downgrade の前に、削除の印が付いたまま残っている行が無いかを確かめる。`deleted_at`の列が消えると、その行は一覧に戻る(dev の DB の複製で確かめた)。残っていれば、先に消すか、戻ってもよいかを決める。

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM projects WHERE deleted_at IS NOT NULL"'
   # → 0 であることを確かめる
   ```

- downgrade は、用途ごとのモデルと登録した API キーのテーブル、生成の失敗の記録と作り直しで渡した指摘の列を消す。**利用者の選んだモデルと登録したキーは消える**(もう一度更新しても戻らない。利用者が登録し直す)。`user_name`は`full_name`に戻り、値は残る。
- 開発環境の DB の複製(利用者 111・プロジェクト 127・用途ごとのモデル 8・API キー 5・生成の失敗の記録 9)で、5本の downgrade → upgrade を2往復して確かめた。どちらの往復でも、upgrade の後はモデル・キー・失敗の記録が 0、利用者・プロジェクトの数と`user_name`の値は変わらない。
- 選んだモデルと登録したキーを残す必要があるなら、downgrade ではなく、1 のバックアップから戻すことを考える。
