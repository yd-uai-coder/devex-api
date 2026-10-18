"""テスト仕様書のテストの入力(要件定義書・外部設計書の model と、AI の下書きの見本)。"""

from app.documents.external_design.model import ApiRow, ExternalDesignModel, ScreenRow
from app.documents.requirements.model import RequirementRow, RequirementsModel
from app.documents.test_spec import CaseDraft, SpecPolicy, TestSpecDraft, Viewpoint
from app.documents.test_spec.drafting import GeneratedCase, TestSpecGenerationOutput

LIST_API = "GET /api/v1/reservations"
CREATE_API = "POST /api/v1/reservations"
DELETE_API = "DELETE /api/v1/reservations/{id}"


def spec_requirements() -> RequirementsModel:
    """Must の機能要件2つ(FR-002 は API・画面に結ばれない)・Should 1つ・非機能要件1つ。"""
    return RequirementsModel(
        purpose="備品の予約",
        rows=[
            RequirementRow(id="FR-001", kind="FR", priority="Must", text="予約できる",
                           source="user"),
            RequirementRow(id="FR-002", kind="FR", priority="Must", text="予約を通知する",
                           source="user"),
            RequirementRow(id="FR-003", kind="FR", priority="Should", text="予約を消せる",
                           source="user"),
            RequirementRow(id="NFR-001", kind="NFR", category="性能", text="3秒以内に応答する",
                           source="assumed"),
        ],
    )


def spec_external() -> ExternalDesignModel:
    """画面2つ(Must・Should)と API 3つ(一覧・登録・削除)。"""
    return ExternalDesignModel(
        screens=[
            ScreenRow(id="SCR-001", name="予約一覧", priority="Must", req_ids=["FR-001"]),
            ScreenRow(id="SCR-002", name="予約の取り消し", priority="Should", req_ids=["FR-003"]),
        ],
        apis=[
            ApiRow(method="GET", path="/api/v1/reservations", screens=["SCR-001"],
                   req_ids=["FR-001"], errors="401 未ログイン"),
            ApiRow(method="POST", path="/api/v1/reservations", screens=["SCR-001"],
                   req_ids=["FR-001"], body="日付・備品", errors="400 入力不正 / 409 重複"),
            ApiRow(method="DELETE", path="/api/v1/reservations/{id}", screens=["SCR-002"],
                   req_ids=["FR-003"], body="なし"),
        ],
    )


def spec_draft() -> TestSpecDraft:
    """結合3件(登録の正常・異常、一覧の正常)と総合1件。削除と FR-002 のテストが無い。"""
    return TestSpecDraft(
        policy=SpecPolicy(target="API 3本", environment="Docker Compose"),
        cases=(
            CaseDraft("integration", "post /api/v1/reservations/", "正常", steps="日付を送る",
                      expected="201", req_ids=("fr-1",), screens=("scr-001",)),
            CaseDraft("integration", "POST /api/v1/reservations", "異常", steps="同じ日付",
                      expected="409", req_ids=("FR-001",)),
            CaseDraft("integration", LIST_API, "正常", expected="200", req_ids=("FR-001",),
                      design_gap="並び順が未定"),
            CaseDraft("system", "SCR-001", "正常", steps="予約する", expected="一覧に出る",
                      req_ids=("FR-001",), screens=("SCR-001",)),
        ),
    )


_OUTPUT_CASES: tuple[tuple[Viewpoint, str], ...] = (("正常", "201"), ("異常", "409"))


def spec_output() -> TestSpecGenerationOutput:
    """テスト仕様書の構造化出力の見本(外部設計書の見本の POST の正常・異常、FR-001)。生成のテストで
    偽の LLM が返す。"""
    cases = [
        GeneratedCase(
            level="integration",
            target=CREATE_API,
            viewpoint=viewpoint,
            precondition="",
            steps="予約を送る",
            expected=expected,
            design_gap="",
            req_ids=["FR-001"],
            screens=["SCR-001"],
        )
        for viewpoint, expected in _OUTPUT_CASES
    ]
    return TestSpecGenerationOutput(
        target="API 1本", out_of_scope="なし", environment="偽 LLM", done_criteria="全件成功",
        cases=cases,
    )
