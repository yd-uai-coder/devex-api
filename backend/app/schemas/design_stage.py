from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.detailed_design import StageState


class StageIssueRead(BaseModel):
    """段階ごとの検証の指摘1件(app/detailed_design/validation.py の StageIssue)。
    `error`があると承認できない。`warning`は承認を止めない。

    段階8(実装可能性チェック)の指摘だけが、重要度`level`(critical=最重要・major=中程度・
    minor=軽微)・直す先の段階`fix_stage`・指摘の出た作業単位の ID`unit`を持つ。簡易モードの
    段階8の指摘は、直す先を文書`fix_document`で示す(`fix_stage`は直す先の段階が無いことを示す8)。"""

    severity: Literal["error", "warning"]
    code: str
    message: str
    target: str | None = None
    level: Literal["critical", "major", "minor"] | None = None
    fix_stage: int | None = None
    unit: str | None = None
    fix_document: (
        Literal["requirements", "external_design", "internal_design", "implementation_plan"] | None
    ) = None


class DfdAccessRead(BaseModel):
    """段階2の DFD の線から決まる、処理とテーブルの関わり1つ(段階3の CRUD 図の固定部分)。
    `table`は ER のテーブル名(ER に無いデータストアは、正規化したデータストア名)。"""

    function_id: str
    table: str
    kind: Literal["read", "write"]


class DesignStageRead(BaseModel):
    """段階1つ分の状態。未着手の段階も含めて、モードの全段階を常に返す(詳細設計モードは段階1〜8、
    簡易モードは段階8だけ。行が無ければversion等はNone)。`mode`はプロジェクトのモード。

    `missing_inputs`は、まだそろっていない入力(`stage:<n>`=承認されていない前の段階、
    `doc:<doc_type>`=まだ無い文書)。空なら段階は開いていて、保存・承認できる。

    `generation_status`はAIの下書きの生成の状態(None=まだ生成していない/generating/completed/
    failed)、`generation_error`は直近の生成が失敗した理由(ユーザー向けの文言)。`issues`は段階ごとの
    検証の結果。`dfd_accesses`は段階3だけが持つ、DFD から決まる R/W(画面で
    DFD を読み直して導き直さないよう、導いた結果を渡す)。`plan`は段階8だけが持つ、作業単位
    (段階7と同じ形。詳細設計モードは承認済みの段階7、簡易モードは実装計画書の WBS を読んだもの。
    段階8が開いていなければ None)。`unit_req_ids`は段階8だけが持つ、作業単位ごとの要件ID
    (詳細設計モードは単位の処理の段階1の要件、簡易モードは WBS の「要件」欄)。`impact_open`・
    `impact_reviewed`は、この段階に記録した変更の影響の印のうち、未確認・確認済みの件数(印の中身は
    変更の影響の取得`DesignImpactRead`で読む)。"""

    stage: int
    mode: Literal["simple", "detailed"] = "detailed"
    state: StageState
    is_open: bool
    missing_inputs: list[str]
    version: int | None
    approved_version: int | None
    model: dict[str, Any] | None
    updated_at: datetime | None
    generation_status: Literal["generating", "completed", "failed"] | None = None
    generation_error: str | None = None
    issues: list[StageIssueRead] = Field(default_factory=list)
    dfd_accesses: list[DfdAccessRead] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    unit_req_ids: dict[str, list[str]] = Field(default_factory=dict)
    impact_open: int = 0
    impact_reviewed: int = 0


class DesignStageSave(BaseModel):
    """段階の保存リクエスト。`version`は画面が見ていた版(未着手の段階を初めて保存するときはNone)。"""

    version: int | None
    model: dict[str, Any]


class DesignStageApprove(BaseModel):
    """段階の承認リクエスト。`version`は画面が見ていた版(見ていない内容を承認しないため)。"""

    version: int


class LogicTarget(BaseModel):
    """段階6で下書きを作る関数1つ(段階4のモジュール一覧のパスと、手順の呼ぶ関数)。"""

    module: str
    function: str


class DesignStageGenerate(BaseModel):
    """段階の下書きの生成リクエスト(本文は省略できる)。`function_ids`は段階5だけが使う、下書きを
    作る処理の処理ID(省略すると、選んだ処理のうちまだ手順の無いもの)。`logics`は段階6
    だけが使う、下書きを作る関数(省略すると、選んだ関数のうちまだ詳細の無いもの)。`unit_ids`は
    段階8だけが使う、手順書を作る作業単位の ID(省略すると、作業単位のうち手順書の無いもの)。"""

    function_ids: list[str] | None = None
    logics: list[LogicTarget] | None = None
    unit_ids: list[str] | None = None


class SequenceIssueRead(BaseModel):
    """シーケンス図にするときの指摘1つ(app/detailed_design/sequence.py の SequenceIssue)。"""

    step_id: str
    code: str
    message: str


class SequenceRead(BaseModel):
    """段階5の処理1つのシーケンス図(保存した手順から導いた SVG と、図にするときの指摘)。"""

    function_id: str
    svg: str
    issues: list[SequenceIssueRead]


class DesignRefRead(BaseModel):
    """段階8の単位が参照する設計1つ(app/detailed_design/procedure_doc_refs.py の ExpandedRef)。
    `markdown`は設計の該当箇所を展開した md(設計に無い参照は None)。`svg`は段階5の手順の
    シーケンス図(手順の参照だけ)。簡易モードの参照は、内部設計書の処理別データフロー
    (`dataflow`)・3.2節のデータモデル全体(`datamodel`。DF を持たない単位だけ)・モジュール一覧の
    層(`module`)。"""

    kind: Literal["procedure", "logic", "module", "dataflow", "datamodel"]
    key: str
    resolved: bool
    via: str | None = None
    label: str
    markdown: str | None = None
    svg: str | None = None


class UnitContextRead(BaseModel):
    """段階8の単位1つの、手順書を読むための材料(参照する設計の展開と、共通の節)。
    `crosscutting`・`environment`は 07章 横断事項と開発環境の md(簡易モードは内部設計書 3.4節と、
    実装計画書 4.3節・内部設計書 3.1節。書かれていなければ空)。"""

    unit_id: str
    refs: list[DesignRefRead]
    crosscutting: str
    environment: str


class UnitAiMarkdownRead(BaseModel):
    """段階8の単位1つの AI 向けの版(画面の「AI 向けにコピー」。zip の`ai/<単位ID>.md`と同じ
    組み立て)。保存済みの手順書から作る。`state`は段階8の状態、`finding_total`・`critical`は
    その単位に残る未定義の件数と、そのうち最重要の件数(画面の警告に使う)。"""

    unit_id: str
    markdown: str
    state: StageState
    finding_total: int
    critical: int


class ElementChangeRead(BaseModel):
    """変わった要素1つ(app/detailed_design/impact の ElementChange)。`source`は出どころ
    (`stage:<N>`=段階の承認・`doc:<文書の種類>`=文書の版の切り替え)。`key`は前のキー(増えたものは
    後のキー)、改名の後のキーは`new_key`。`before`・`after`は要素の内容(改名は前後の名前)。
    `pending`は、まだ承認していない編集の差分(承認すると記録される)。"""

    source: str
    key: str
    change: Literal["added", "removed", "content", "rename"]
    stage: int
    label: str
    before: str | None = None
    after: str | None = None
    new_key: str | None = None
    pending: bool = False


class ImpactMarkRead(BaseModel):
    """見直しが要る要素1つの印。`path`は変わった要素からこの要素までの道、`reason`は最後の辺の
    種類から決まる理由の文、`sources`は届いた変更の出どころ。"""

    key: str
    label: str
    stage: int
    path: list[str]
    reason: str
    sources: list[str]
    reviewed: bool = False
    pending: bool = False


class StageImpactRead(BaseModel):
    """段階1つの、変わったものと印。`open`・`reviewed`は記録した印(pending を除く)の件数。
    他の段階・文書から届いた未確認の印が残っていると、この段階は承認できない。"""

    stage: int
    changes: list[ElementChangeRead] = Field(default_factory=list)
    marks: list[ImpactMarkRead] = Field(default_factory=list)
    open: int = 0
    reviewed: int = 0


class RegenerationTargetRead(BaseModel):
    """再生成の候補1つ(部分再生成できる段階5・6・8 の、印の付いた要素)。自動では再生成しない。"""

    stage: int
    key: str
    label: str


class DesignImpactRead(BaseModel):
    """プロジェクトの変更の影響(作業領域の「変更の影響」パネルが読む)。`stages`はモードの全段階。"""

    stages: list[StageImpactRead]
    regeneration_targets: list[RegenerationTargetRead] = Field(default_factory=list)


class ImpactReview(BaseModel):
    """印の確認済みの切り替えリクエスト。`keys`の印を`reviewed`にする(知らないキーは無視する)。"""

    keys: list[str] = Field(min_length=1)
    reviewed: bool = True
