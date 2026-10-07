"""段階8(実装手順書)のAIの下書きの入出力(純粋関数。docs/external_design.md 2.7節)。

1つの作業単位の手順書を、LLM 1回で下書きする(単位ごとに生成・作り直す。段階5・6と同じ形)。
入力は、段階7の単位(種別・処理・依存・モジュール・環境・設定のファイル)と、単位が参照する設計を
展開したもの(`unit_context`)、段階7の 07 横断事項と開発環境。設計の全文は渡さない。

- 単位の ID とタスク名は書かせない(段階7から写す。手順書と段階7を突き合わせる鍵のため)。
- 設計に書いてある振る舞いは書き写させない。設計に無いために決められないことは、推測で埋めずに
  AI の指摘(`findings`)として挙げさせる。直すのは設計の側(段階1〜7)。
"""


from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.plan import PLAN_STAGE
from app.detailed_design.procedure_doc import (
    PROCEDURE_DOC_STAGE,
    AiFinding,
    FindingLevel,
    PlanUnit,
    TestPoint,
    UnitFile,
    UnitFileKind,
    UnitProcedure,
)
from app.detailed_design.procedure_doc_refs import UnitContext
from app.detailed_design.prompt_rules import NAMING_RULES

_KIND_TEXT = {"feature": "機能", "base": "基盤"}


class GeneratedUnitFile(BaseModel):
    """AIが下書きする、作成・変更するファイル1つ。"""

    path: str = Field(description="ファイルのパス")
    kind: UnitFileKind = Field(
        description="module=段階4のモジュール / test=テスト / config=環境・設定のファイル"
    )
    responsibility: str = Field(description="このファイルで行うこと(1文)")
    basis: str = Field(
        description="根拠(『段階4』『段階7 環境・設定のファイル』『07章 認証』"
        "『手順書で決める』など)"
    )


class GeneratedTestPoint(BaseModel):
    """AIが下書きするテスト観点1つ。"""

    viewpoint: str = Field(description="観点(何が成り立つことを確かめるか)")
    sut: str = Field(description="SUT(テスト対象の関数・API・画面)")
    driver: str = Field(
        description="ドライバ(テストの種類と呼び出し方。単体テスト・結合テストなど)"
    )
    stub: str = Field(
        description="スタブ(差し替える依存と理由)。不要なら『スタブ不要 ── 理由』"
    )


class GeneratedFinding(BaseModel):
    """AIが下書きする指摘1つ(設計に無いために決められないこと)。"""

    level: FindingLevel = Field(
        description="critical=決まらないと実装に着手できない / major=実装はできるが手戻りになりうる"
        " / minor=細部"
    )
    target: str = Field(description="対象(『段階3 reservations』『07章 認証』『F-01』など)")
    message: str = Field(description="何が決まっていないか(1〜2文)")
    fix_stage: int = Field(
        description="それを決めるべき段階の番号(1=機能一覧 2=処理概要と DFD 3=データモデルと CRUD"
        " 4=モジュール一覧 5=主要処理の手順 6=処理ロジックの詳細 7=横断事項と実装計画)"
    )


class ProcedureDocGenerationOutput(BaseModel):
    """単位1つ分の構造化出力。"""

    purpose: str = Field(description="この単位が終わるとできるようになること(1〜2文)")
    files: list[GeneratedUnitFile] = Field(description="作成・変更するファイル(依存される順)")
    notes: list[str] = Field(
        description="実装の要点(設計に無いが自明な作業と、単位の中の作業の順序の理由だけ)"
    )
    tests: list[GeneratedTestPoint] = Field(description="テスト観点")
    gwt: list[str] = Field(description="主な観点の Given / When / Then を1行ずつ")
    verify: list[str] = Field(description="この単位が終わったことを確かめる方法")
    findings: list[GeneratedFinding] = Field(
        description="設計に無いために決められないこと。無ければ空"
    )


PROCEDURE_DOC_SYSTEM_PROMPT = (
    "あなたは実装手順書の作成者です。【対象の単位】1つについて、設計を実装に移すための手順書"
    "(目的 / 作成・変更するファイル / 実装の要点 / テスト観点 / 確認方法 / 未定義)を作成します。"
    "読み手は、この手順書と【参照する設計】を見ながら実装する開発者(またはコーディング AI)です。\n"
    "規則:\n"
    "- 設計を書き写さない。【参照する設計】にある振る舞い(条件・ステータス・並び順・例外の応答)は"
    "手順書に書かず、参照に任せる\n"
    "- 設計に無いために決められないことは、推測で埋めずに findings に挙げる。"
    "fix_stage には、それを決めるべき段階の番号(1〜7)を書く。"
    "外部設計・要件定義に無いことは 1 にする\n"
    "- 『設計にありません』とある参照は、検証が別に指摘する。参照が無いこと自体は findings に"
    "挙げず、そのために決められない具体的な内容を挙げる\n"
    "- files は依存される順(先に作るもの)に並べる。kind=module のパスは【対象の単位】の"
    "モジュールを一字一句そのまま使う。テストのファイルは kind=test、環境・設定のファイルは"
    "kind=config にする\n"
    "- notes には、設計に無いが自明な作業"
    "(マイグレーションの追加・既存の部品の再利用・設定の追加)と、"
    "単位の中の作業の順序の理由だけを書く。振る舞いを決めることは書かない\n"
    "- tests は観点ごとに SUT・ドライバ・スタブを書く。gwt には主な観点の Given / When / Then を"
    "1行ずつ書く。境界値や非機能などの体系的なテスト設計は書かない\n"
    "- verify には、この単位が終わったことを確かめる方法(テストが通る・画面での操作など)を書く\n"
    "- 種別が基盤の単位は処理を持たない。"
    "環境・設定のファイル、07章 横断事項、開発環境を根拠にする\n"
    "- 処理ID・パス・テーブル名・関数名は入力の値をそのまま使う" + NAMING_RULES
)


def _unit_text(unit: PlanUnit) -> str:
    task = unit.task
    return "\n".join(
        [
            f"- 単位: {unit.unit_id} {task.title}",
            f"- 種別: {_KIND_TEXT.get(task.kind, task.kind)}",
            f"- マイルストーン: {unit.milestone}",
            f"- 処理: {', '.join(task.function_ids) or 'なし'}",
            f"- 依存する単位: {', '.join(task.depends_on) or 'なし'}",
            f"- モジュール(段階4): {', '.join(task.modules) or 'なし'}",
            f"- 環境・設定のファイル(例): {', '.join(task.config_files) or 'なし'}",
        ]
    )


def build_procedure_doc_messages(context: UnitContext) -> list[BaseMessage]:
    """1つの単位の手順書の下書きの入力。"""
    refs = [ref.markdown or f"- {ref.label}: 設計にありません" for ref in context.refs]
    common = [section for section in (context.crosscutting, context.environment) if section]
    content = "\n\n".join(
        [
            f"## 対象の単位\n{_unit_text(context.unit)}",
            "## 参照する設計\n\n" + ("\n\n".join(refs) or "(ありません)"),
            "## 共通の方針(段階7)\n\n" + ("\n\n".join(common) or "(ありません)"),
        ]
    )
    return [SystemMessage(content=PROCEDURE_DOC_SYSTEM_PROMPT), HumanMessage(content=content)]


def _texts(values: list[str]) -> list[str]:
    return [v.strip() for v in values if v.strip()]


def _fix_stage(stage: int) -> int:
    """直す先の段階を 1〜7 に限る。範囲の外は段階8(「段階Nで直す」を出さない)にする。"""
    return stage if 1 <= stage <= PLAN_STAGE else PROCEDURE_DOC_STAGE


def to_unit_procedure(unit: PlanUnit, output: ProcedureDocGenerationOutput) -> UnitProcedure:
    """構造化出力を、単位の手順書にする(ID とタスク名は段階7から写す)。空の行は捨てる。"""
    return UnitProcedure(
        unit_id=unit.unit_id,
        title=unit.task.title,
        purpose=output.purpose.strip(),
        files=[
            UnitFile(
                path=f.path.strip(),
                kind=f.kind,
                responsibility=f.responsibility.strip(),
                basis=f.basis.strip(),
            )
            for f in output.files
            if f.path.strip()
        ],
        notes=_texts(output.notes),
        tests=[
            TestPoint(
                viewpoint=t.viewpoint.strip(),
                sut=t.sut.strip(),
                driver=t.driver.strip(),
                stub=t.stub.strip(),
            )
            for t in output.tests
            if t.viewpoint.strip()
        ],
        gwt=_texts(output.gwt),
        verify=_texts(output.verify),
        findings=[
            AiFinding(
                level=f.level,
                target=f.target.strip(),
                message=f.message.strip(),
                fix_stage=_fix_stage(f.fix_stage),
            )
            for f in output.findings
            if f.message.strip()
        ],
    )
