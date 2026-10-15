"""テスト仕様書の意味モデル(`generated_documents.model`に JSON で持つ)。

結合テスト(API 単位)と総合テスト(画面の流れ・非機能)の行を別の表に持つ。行の ID(`IT-001`・
`ST-001`)は生成のたびに並び順で振る(後続の文書や段階から参照されないので引き継がない)。

- 処理・単位(`functions`・`units`)はコードが API から引いて埋める(`links.api_links`)。
- 要件・API・画面は生成した時点の写し(`known_*`)と照らして検証する。写しが空なら照合しない。
- `inputs`は生成した時点の入力の版(`doc:<種類>`・`stage:<N>`)。今の版と違えば「古い」。
"""

from typing import Literal, get_args

from pydantic import BaseModel, Field

from app.documents.ids import seq_id

CaseLevel = Literal["integration", "system"]
Viewpoint = Literal["正常", "異常", "境界", "セキュリティ", "非機能"]

VIEWPOINTS: tuple[Viewpoint, ...] = get_args(Viewpoint)
ERROR_VIEWPOINT: Viewpoint = "異常"
TEST_PREFIX: dict[CaseLevel, str] = {"integration": "IT", "system": "ST"}


class SpecPolicy(BaseModel):
    """1 テストの方針(観点は固定なので持たない)。"""

    target: str = ""
    out_of_scope: str = ""
    environment: str = ""
    done_criteria: str = ""


class SpecCase(BaseModel):
    """テスト1件。結合テストの`target`は API(`POST /api/v1/x`)、総合テストは画面の流れ・運用など。
    `design_gap`は期待結果に要るのに設計で決まっていないこと(空なら無い)。"""

    id: str
    level: CaseLevel
    target: str
    viewpoint: Viewpoint
    precondition: str = ""
    steps: str = ""
    expected: str = ""
    design_gap: str = ""
    req_ids: list[str] = Field(default_factory=list)
    screens: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    units: list[str] = Field(default_factory=list)


class TestSpecModel(BaseModel):
    """テスト仕様書1版の中身。"""

    __test__ = False  # pytest に集めさせない(名前が Test で始まるため)

    policy: SpecPolicy = Field(default_factory=SpecPolicy)
    integration: list[SpecCase] = Field(default_factory=list)
    system: list[SpecCase] = Field(default_factory=list)
    # 生成した時点の要件定義書の要件ID(優先度は機能要件だけ)と Must の機能要件ID
    known_req_ids: list[str] = Field(default_factory=list)
    req_priorities: dict[str, str] = Field(default_factory=dict)
    must_fr_ids: list[str] = Field(default_factory=list)
    # 生成した時点の外部設計書の API(`POST /api/v1/x`)と画面ID
    known_apis: list[str] = Field(default_factory=list)
    known_screens: list[str] = Field(default_factory=list)
    # 生成した時点の入力の版(承認されていない段階は None)
    inputs: dict[str, int | None] = Field(default_factory=dict)

    @property
    def cases(self) -> list[SpecCase]:
        return [*self.integration, *self.system]

    def tests_for(self, req_id: str) -> list[str]:
        """要件ID を持つテストの ID(結合 → 総合の順)。"""
        return [c.id for c in self.cases if req_id in c.req_ids]


def case_id(level: CaseLevel, seq: int) -> str:
    return seq_id(TEST_PREFIX[level], seq)
