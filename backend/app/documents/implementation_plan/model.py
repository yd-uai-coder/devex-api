"""実装計画書の意味モデル(簡易ドキュメントモード。`generated_documents.model`に JSON で持つ)。

マイルストーン・タスク・リスクは段階7(詳細設計モードの実装計画)と同じ型(`Milestone`・
`PlanTask`・`Risk`)を使う。タスクは実装手順書の作業単位で、段階8は`to_plan`で段階7と同じ
`PlanModel`として読む。タスクの`function_ids`は内部設計書の処理別データフローの ID(`DF-1`)。

- マイルストーンの番号(`M-01`)と単位の ID(`M-01-T01`)は保存しない(段階7と同じく並び順から
  導く)。タスクは安定キー(`key`。`U-001`…)を持ち、再生成ではタスク名で前の版のキーを引き継ぐ。
- 説明の節(4.1 フェーズ分割・4.3 開発環境)は本文の文字列。
"""

from pydantic import BaseModel, Field

from app.detailed_design.plan import Milestone, PlanModel, Risk


class ImplementationPlanModel(BaseModel):
    """実装計画書1版の中身。"""

    phases: str = ""  # 4.1 開発フェーズ分割・マイルストーン
    milestones: list[Milestone] = Field(default_factory=list)  # 4.2 タスク分解(WBS)
    environment: str = ""  # 4.3 開発環境・CI/CD・事前準備事項
    risks: list[Risk] = Field(default_factory=list)  # 4.4 想定リスクと対策
    # 次に振る単位のキーの番号
    next_unit_seq: int = 1
    # 生成した時点の内部設計書の処理別データフローの ID と、要件定義書の要件ID・Must の機能要件ID。
    # 検証が読む(写しが空なら、その照合はしない)
    known_dataflow_ids: list[str] = Field(default_factory=list)
    known_req_ids: list[str] = Field(default_factory=list)
    must_fr_ids: list[str] = Field(default_factory=list)

    def to_plan(self) -> PlanModel:
        """段階7と同じ形の計画(横断事項は持たない)。"""
        return PlanModel(
            milestones=self.milestones,
            environment=self.environment,
            risks=self.risks,
            next_unit_seq=self.next_unit_seq,
        )
