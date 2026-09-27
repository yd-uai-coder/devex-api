#!/bin/sh
set -e

# 統合テスト(tests/integration/、pytest -m integration)専用のデータベースを作成する。
# devex-api/CLAUDE.md「テストの分離」節参照 ── 開発用DB($POSTGRES_DB)と同じDBを
# 統合テストに使うと、tests/integration/conftest.pyのBase.metadata.create_all/drop_all
# が開発用データ・スキーマを消してしまう事故が実際に発生したため分離した。
#
# postgresの公式イメージの仕様上、docker-entrypoint-initdb.d配下のスクリプトは
# Postgresデータボリュームが空の状態で初回起動したときのみ実行される。
# 既存のボリュームに対しては手動で `CREATE DATABASE` を実行すること。
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -c "SELECT 'CREATE DATABASE \"$POSTGRES_TEST_DB\"' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$POSTGRES_TEST_DB')\gexec"
