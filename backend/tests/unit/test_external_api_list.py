"""外部設計書の「2.6 API一覧」(プロンプトと表の読み取り)のテスト。

SUT: extract_api_endpoints / parse_trigger / trigger_key / endpoint_key
     (app/detailed_design/api_list.py)、
     外部設計書の構造化出力のスキーマ(app/documents/external_design/drafting.py)・
     内部設計書の構造化出力のスキーマ(app/documents/internal_design/drafting.py)、
     E2E用の偽LLMが返す外部設計書(app/ai/llm/fake.py。md は生成と同じく組み立てる)
ドライバ: 各テスト関数
スタブ不要 ── 表の読み取りは純粋関数で、プロンプトは定数のため。
"""

from tests.fixtures.external_design import e2e_fake_external_design_md

from app.ai.llm.fake import E2eFakeLLM
from app.detailed_design import endpoint_key, extract_api_endpoints, parse_trigger, trigger_key
from app.documents.external_design.drafting import GeneratedApi
from app.documents.internal_design.drafting import GeneratedInternalApi

EXTERNAL_DESIGN = """# 2. 外部設計書

## 2.2 画面一覧・画面遷移フロー（概要）
| 画面ID | 画面名 | 主要な役割 | 優先度 |
|---|---|---|---|
| SCR-001 | ログイン | 認証 | Must |

## 2.6 API一覧
| メソッド | パス | 概要 | 関連画面 |
|:---|:---|---|---|
| POST | `/api/v1/projects` | プロジェクトを作成する | SCR-003 |
| get | /api/v1/projects/{project_id}/documents | 文書を返す | SCR-005/SCR-006 |
| POST | /api/v1/projects | 重複した行 | SCR-003 |
| GET | /health | 死活を返す | — |
| — | 表の外の注記 | | |

## 2.7 その他
| メソッド | パス | 概要 |
|---|---|---|
| DELETE | /api/v1/ignored | 2.6 の外 |
"""


async def test_e2e_fake_external_design_has_parsable_api_list() -> None:
    """統合スモーク: 偽LLMの外部設計書(構造化出力 → model → md)が、表の読み取りを通る。"""
    endpoints = extract_api_endpoints(await e2e_fake_external_design_md(E2eFakeLLM()))

    assert [e.key for e in endpoints] == ["POST /api/v1/reservations", "GET /api/v1/reservations"]


def test_external_design_schema_asks_for_api_path_form() -> None:
    """パスの形(段階1の機能グループの初期値が読む)は、構造化出力のスキーマで指示する。"""
    description = GeneratedApi.model_fields["path"].description or ""

    assert "/api/v1/<リソース>" in description
    assert "{id}" in description


def test_internal_design_schema_reuses_external_api_list() -> None:
    description = GeneratedInternalApi.model_fields["path"].description or ""

    assert "【外部設計書】2.6 の API と同じパス" in description


def test_extract_api_endpoints_reads_only_section_2_6() -> None:
    endpoints = extract_api_endpoints(EXTERNAL_DESIGN)

    assert [(e.method, e.path) for e in endpoints] == [
        ("POST", "/api/v1/projects"),
        ("GET", "/api/v1/projects/{project_id}/documents"),
        ("GET", "/health"),
    ]
    assert endpoints[0].summary == "プロジェクトを作成する"
    assert endpoints[1].screens == ("SCR-005", "SCR-006")
    assert endpoints[2].screens == ()


def test_extract_api_endpoints_without_section_is_empty() -> None:
    assert extract_api_endpoints("# 2. 外部設計書\n\n## 2.2 画面一覧\n") == []


def test_endpoint_key_ignores_parameter_names_and_trailing_slash() -> None:
    assert endpoint_key("get", "/api/v1/projects/{id}/") == endpoint_key(
        "GET", "`/api/v1/projects/{project_id}`"
    )


def test_parse_trigger_accepts_annotation_and_rejects_batches() -> None:
    assert parse_trigger("POST /api/v1/projects/{id}/generate(BackgroundTasks)") == (
        "POST",
        "/api/v1/projects/{id}/generate",
    )
    assert parse_trigger("毎日 0時の集計バッチ") is None
    assert trigger_key("POST /api/v1/projects/{x}/generate") == "POST /api/v1/projects/{}/generate"
