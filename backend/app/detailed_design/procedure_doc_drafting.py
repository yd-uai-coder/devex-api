"""段階8(実装手順書)のAIの下書きの入出力(純粋関数。docs/external_design.md 2.7節)。

1つの作業単位の手順書を、LLM 1回で下書きする(単位ごとに生成・作り直す。段階5・6と同じ形)。
入力は、段階7の単位(種別・処理・依存・モジュール・環境・設定のファイル)と、単位が参照する設計を
展開したもの(`unit_context`)、段階7の 07 横断事項と開発環境。設計の全文は渡さない。

- 単位の ID とタスク名は書かせない(段階7から写す。手順書と段階7を突き合わせる鍵のため)。
- 設計に書いてある振る舞いは書き写させない。設計に無いために決められないことは、推測で埋めずに
  AI の指摘(`findings`)として挙げさせる。直すのは設計の側(段階1〜7)。
- 簡易ドキュメントモードは、入力の単位が実装計画書の WBS、参照が内部設計書(`simple_unit_context`)
  で、指摘の直す先を段階でなく文書で書かせる(`SimpleProcedureDocGenerationOutput`)。関数の契約と
  手順が無いので「未定義」が多く出る。それを隠さず挙げさせ、手順の要る単位には詳細設計モードを勧める。
"""


from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.plan import PLAN_STAGE
from app.detailed_design.procedure_doc import (
    PROCEDURE_DOC_STAGE,
    AiFinding,
    DesignDocument,
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


class GeneratedSimpleFinding(BaseModel):
    """簡易モードで AI が下書きする指摘1つ。直す先は段階でなく文書。"""

    level: FindingLevel = Field(
        description="critical=決まらないと実装に着手できない / major=実装はできるが手戻りになりうる"
        " / minor=細部"
    )
    target: str = Field(description="対象(『DF-1』『3.2 reservations』『3.4』など)")
    message: str = Field(description="何が決まっていないか(1〜2文)")
    fix_document: DesignDocument = Field(
        description="それを決めるべき文書(requirements=要件定義書 external_design=外部設計書"
        " internal_design=内部設計書 implementation_plan=実装計画書)"
    )


class _ProcedureDocDraft(BaseModel):
    """単位1つ分の構造化出力のうち、両モードに共通の欄。"""

    purpose: str = Field(description="この単位が終わるとできるようになること(1〜2文)")
    files: list[GeneratedUnitFile] = Field(description="作成・変更するファイル(依存される順)")
    notes: list[str] = Field(
        description="実装の要点(設計に無いが自明な作業と、単位の中の作業の順序の理由だけ)"
    )
    tests: list[GeneratedTestPoint] = Field(description="テスト観点")
    gwt: list[str] = Field(description="主な観点の Given / When / Then を1行ずつ")
    verify: list[str] = Field(description="この単位が終わったことを確かめる方法")


class ProcedureDocGenerationOutput(_ProcedureDocDraft):
    """単位1つ分の構造化出力。"""

    findings: list[GeneratedFinding] = Field(
        description="設計に無いために決められないこと。無ければ空"
    )


class SimpleProcedureDocGenerationOutput(_ProcedureDocDraft):
    """簡易モードの、単位1つ分の構造化出力(指摘の直す先が文書)。"""

    findings: list[GeneratedSimpleFinding] = Field(
        description="設計に無いために決められないこと。無ければ空"
    )


_INTRO = (
    "あなたは実装手順書の作成者です。【対象の単位】1つについて、設計を実装に移すための手順書"
    "(目的 / 作成・変更するファイル / 実装の要点 / テスト観点 / 確認方法 / 未定義)を作成します。"
    "読み手は、この手順書と【参照する設計】を見ながら実装する開発者(またはコーディング AI)です。\n"
    "規則:\n"
    "- 設計を書き写さない。【参照する設計】にある振る舞い(条件・ステータス・並び順・例外の応答)は"
    "手順書に書かず、参照に任せる\n"
)
_COMMON_RULES = (
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
)

PROCEDURE_DOC_SYSTEM_PROMPT = (
    _INTRO
    + "- 設計に無いために決められないことは、推測で埋めずに findings に挙げる。"
    "fix_stage には、それを決めるべき段階の番号(1〜7)を書く。"
    "外部設計・要件定義に無いことは 1 にする\n"
    + _COMMON_RULES
    + "- 種別が基盤の単位は処理を持たない。"
    "環境・設定のファイル、07章 横断事項、開発環境を根拠にする\n"
    "- 処理ID・パス・テーブル名・関数名は入力の値をそのまま使う" + NAMING_RULES
)

# 簡易ドキュメントモード。参照は内部設計書の処理別データフロー(DF)とモジュール一覧で、関数の契約と
# 処理の手順は無い(作成方針 3・10章)
SIMPLE_PROCEDURE_DOC_SYSTEM_PROMPT = (
    _INTRO
    + "- 設計に無いために決められないことは、推測で埋めずに findings に挙げる。"
    "fix_document には、それを決めるべき文書(requirements / external_design / "
    "internal_design / implementation_plan)を書く\n"
    "- 参照する設計は内部設計書の処理別データフロー(DF)・モジュール一覧・テーブルと、"
    "外部設計書の API 一覧だけで、関数の契約(引数・戻り値・例外)と処理の手順は無い。"
    "それで決められないことは findings に挙げる。流れの表だけでは処理の手順が決まらない単位は、"
    "findings で詳細設計モードで詰めることを勧める\n"
    + _COMMON_RULES
    + "- 種別が基盤の単位は処理を持たない。"
    "環境・設定のファイル、内部設計書 3.4節、開発環境を根拠にする\n"
    "- DF の ID・パス・テーブル名は入力の値をそのまま使う" + NAMING_RULES
)


def _unit_text(unit: PlanUnit, module_source: str) -> str:
    task = unit.task
    return "\n".join(
        [
            f"- 単位: {unit.unit_id} {task.title}",
            f"- 種別: {_KIND_TEXT.get(task.kind, task.kind)}",
            f"- マイルストーン: {unit.milestone}",
            f"- 処理: {', '.join(task.function_ids) or 'なし'}",
            f"- 依存する単位: {', '.join(task.depends_on) or 'なし'}",
            f"- モジュール({module_source}): {', '.join(task.modules) or 'なし'}",
            f"- 環境・設定のファイル(例): {', '.join(task.config_files) or 'なし'}",
        ]
    )


def build_procedure_doc_messages(context: UnitContext) -> list[BaseMessage]:
    """1つの単位の手順書の下書きの入力。"""
    content = _content(context, module_source="段階4", common_source="段階7")
    return [SystemMessage(content=PROCEDURE_DOC_SYSTEM_PROMPT), HumanMessage(content=content)]


def build_simple_procedure_doc_messages(context: UnitContext) -> list[BaseMessage]:
    """簡易モードの、1つの単位の手順書の下書きの入力。"""
    content = _content(
        context, module_source="内部設計書 3.3", common_source="内部設計書・実装計画書"
    )
    return [
        SystemMessage(content=SIMPLE_PROCEDURE_DOC_SYSTEM_PROMPT),
        HumanMessage(content=content),
    ]


def _content(context: UnitContext, *, module_source: str, common_source: str) -> str:
    refs = [ref.markdown or f"- {ref.label}: 設計にありません" for ref in context.refs]
    common = [section for section in (context.crosscutting, context.environment) if section]
    return "\n\n".join(
        [
            f"## 対象の単位\n{_unit_text(context.unit, module_source)}",
            "## 参照する設計\n\n" + ("\n\n".join(refs) or "(ありません)"),
            f"## 共通の方針({common_source})\n\n" + ("\n\n".join(common) or "(ありません)"),
        ]
    )


def _texts(values: list[str]) -> list[str]:
    return [v.strip() for v in values if v.strip()]


def _fix_stage(stage: int) -> int:
    """直す先の段階を 1〜7 に限る。範囲の外は段階8(「段階Nで直す」を出さない)にする。"""
    return stage if 1 <= stage <= PLAN_STAGE else PROCEDURE_DOC_STAGE


def _finding(finding: GeneratedFinding | GeneratedSimpleFinding) -> AiFinding:
    if isinstance(finding, GeneratedSimpleFinding):
        fix_stage, fix_document = PROCEDURE_DOC_STAGE, finding.fix_document
    else:
        fix_stage, fix_document = _fix_stage(finding.fix_stage), None
    return AiFinding(
        level=finding.level,
        target=finding.target.strip(),
        message=finding.message.strip(),
        fix_stage=fix_stage,
        fix_document=fix_document,
    )


def to_unit_procedure(
    unit: PlanUnit, output: ProcedureDocGenerationOutput | SimpleProcedureDocGenerationOutput
) -> UnitProcedure:
    """構造化出力を、単位の手順書にする(ID とタスク名は作業単位から写す)。空の行は捨てる。"""
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
        findings=[_finding(f) for f in output.findings if f.message.strip()],
    )
