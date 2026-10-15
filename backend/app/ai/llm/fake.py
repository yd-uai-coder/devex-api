"""ブラウザ経由のE2Eテスト専用の決定論的LLMスタブ。

`tests/fixtures/fake_llm.py`のFakeLLM(pytestが個々のテストでコンストラクタ引数として
台本を注入する設計)とは別物。こちらは`docker compose`で起動する実プロセスに対して
`E2E_FAKE_LLM=true`という環境変数経由でのみ有効化され(app/ai/llm/gemini.py参照)、
Playwrightのブラウザ操作からPythonプロセス内部へ台本を注入する経路が無い。
そのため、LLMに渡された`messages`自体(システムプロンプトの文言・HumanMessageの件数)を
手がかりに、外部から一切設定を注入されなくても意味のある応答を返せるよう、ステートレスかつ
決定論的に応答を組み立てる設計にした。
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from app.detailed_design.data_flow_drafting import (
    GeneratedGroupProcess,
    GeneratedSummary,
    GroupDfdGenerationOutput,
    ProcessSummaryGenerationOutput,
)
from app.detailed_design.data_model_drafting import (
    CrudGenerationOutput,
    DataModelErOutput,
    DraftedColumn,
    DraftedTable,
    GeneratedCrudCell,
)
from app.detailed_design.drafting import FunctionListGenerationOutput, GeneratedFunction
from app.detailed_design.logic_drafting import GeneratedPseudoStep, LogicGenerationOutput
from app.detailed_design.plan_drafting import (
    CrossCuttingGenerationOutput,
    GeneratedCrossCutting,
    GeneratedMilestone,
    GeneratedRisk,
    GeneratedTask,
    PlanGenerationOutput,
)
from app.detailed_design.procedure_doc_drafting import (
    GeneratedFinding,
    GeneratedSimpleFinding,
    GeneratedTestPoint,
    GeneratedUnitFile,
    ProcedureDocGenerationOutput,
    SimpleProcedureDocGenerationOutput,
)
from app.detailed_design.procedure_drafting import (
    GeneratedBranch,
    GeneratedStep,
    ProcedureGenerationOutput,
)
from app.detailed_design.structure_drafting import GeneratedModuleRow, ModuleListGenerationOutput
from app.documents.external_design.drafting import (
    ExternalDesignGenerationOutput,
    GeneratedApi,
    GeneratedScreen,
)
from app.documents.implementation_plan import drafting as plan_doc
from app.documents.internal_design import drafting as internal_doc
from app.documents.requirements.drafting import (
    GeneratedRequirement,
    GeneratedUserScene,
    RequirementsGenerationOutput,
)
from app.hearing import POINT_DEFS, is_point_key
from app.review.fix_check import FixCheckOut
from app.review.schemas import ReviewFindingOut, ReviewOutput
from app.schemas.hearing import (
    CardOptionOut,
    CardOut,
    HearingPointsJudgement,
    HearingReplyOut,
    JudgedPointOut,
)
from app.uml.generation.schemas import (
    ComponentGenerationOutput,
    GeneratedDataItem,
    GeneratedDataItemField,
    GeneratedDependency,
    GeneratedFlow,
    GeneratedModule,
    GeneratedNode,
)

# 観点の表の必須をすべて決めたと返すまでに要するHumanMessage数(末尾の判定プロンプト自身を
# 除く)。「初期ヒアリング入力(intake)1件+実際のチャット発話3件」で4件になる。
# chat_service.judge_pointsは末尾に判定プロンプト自身のHumanMessageを追記するため、
# これも数えてしまうと実際のチャット発話が1件少なくても条件を満たしてしまう。
_TURNS_UNTIL_SUFFICIENT = 4

# 判定のLLMに見せる発言の番号([#n])。根拠の番号として返す
_MESSAGE_NUMBER = re.compile(r"^\[#(\d+)\] ")

# 返信のシステムプロンプトにある「カードにしてよい観点」の行(- キー: 観点(例: ...))
_CARD_TARGET = re.compile(r"^- ([a-z_]+): ", re.MULTILINE)

# 内部設計書の下書き。簡易モードの実装手順書が読むテーブル・API 一覧・モジュール一覧・処理別データ
# フローを持つ(md は生成と同じく model から組み立てる)。処理は外部設計書の POST の1つだけ。
_INTERNAL_DESIGN_OUTPUT = internal_doc.InternalDesignGenerationOutput(
    architecture="[E2E Fake] api / service / repository の3層構成",
    data_model_note="- reservations: 予約",
    tables=[
        internal_doc.GeneratedTable(
            name="reservations",
            description="予約",
            columns=[
                internal_doc.GeneratedColumn(
                    name="id", type="UUID", constraints="PK", description="予約ID"
                )
            ],
        )
    ],
    module_policy="- api → service → repository",
    apis=[
        internal_doc.GeneratedInternalApi(
            method="POST",
            path="/api/v1/reservations",
            summary="予約を登録する(ルート → サービス)",
        )
    ],
    modules=[
        internal_doc.GeneratedModule(
            path="app/main.py",
            layer="起動",
            responsibility="アプリの組み立て",
            depends_on=["app/api/reservations.py"],
        ),
        internal_doc.GeneratedModule(
            path="app/api/reservations.py",
            layer="api",
            responsibility="予約のルート",
            depends_on=["app/services/reservation.py"],
        ),
        internal_doc.GeneratedModule(
            path="app/services/reservation.py",
            layer="service",
            responsibility="予約の検証と保存",
            depends_on=[],
        ),
    ],
    dataflows=[
        internal_doc.GeneratedDataFlow(
            trigger="POST /api/v1/reservations",
            flows=[
                internal_doc.GeneratedFlow(
                    source="利用者",
                    data="予約リクエスト",
                    transform="検証して保存",
                    target="reservations",
                )
            ],
            data_items=[
                internal_doc.GeneratedDataItem(
                    name="予約リクエスト", fields=["item_id", "start_at"]
                )
            ],
        )
    ],
    error_policy="- エラーは {code, message} の形で返す",
)

# 実装計画書の下書き。簡易モードの実装手順書が作業単位として読むので、_INTERNAL_DESIGN_OUTPUT の
# DF・モジュールにそろえる。「要件」は偽の要件定義書(_REQUIREMENTS)の Must の FR-001。
_IMPLEMENTATION_PLAN_OUTPUT = plan_doc.ImplementationPlanGenerationOutput(
    phases="[E2E Fake] フェーズ1(M-01) ── 【Must】",
    milestones=[
        plan_doc.GeneratedMilestone(
            name="予約の登録",
            priority="Must",
            goal="備品を予約できる",
            tasks=[
                plan_doc.GeneratedTask(
                    id="M-01-T01",
                    kind="base",
                    title="開発環境を用意する",
                    dataflow_ids=[],
                    req_ids=[],
                    depends_on=[],
                    modules=["app/main.py"],
                    config_files=["docker-compose.yml", "alembic/versions/0001_reservations.py"],
                ),
                plan_doc.GeneratedTask(
                    id="M-01-T02",
                    kind="feature",
                    title="予約を登録する",
                    dataflow_ids=["DF-1"],
                    req_ids=["FR-001"],
                    depends_on=["M-01-T01"],
                    modules=["app/api/reservations.py", "app/services/reservation.py"],
                    config_files=[],
                ),
            ],
        )
    ],
    environment="- Docker Compose で API と DB を起動する",
    risks=[plan_doc.GeneratedRisk(risk="予約の重複", mitigation="一意制約で防ぐ")],
)

# 外部設計書の下書き。詳細設計モードの段階1(機能一覧)が 2.6 の API 一覧を読むので、
# 下の機能一覧の下書きと同じ2つの API にする。要件ID は偽の要件定義書(_REQUIREMENTS)の
# FR-001(予約)・FR-002(履歴)に合わせる。
_EXTERNAL_DESIGN_OUTPUT = ExternalDesignGenerationOutput(
    overview="[E2E Fake] ブラウザ → API → PostgreSQL の構成",
    screens=[
        GeneratedScreen(
            id="SCR-001",
            name="予約画面",
            role="備品を予約し、予約の一覧を見る",
            priority="Must",
            req_ids=["FR-001", "FR-002"],
            ui_spec="- 入力: 備品・期間\n- 操作: 予約する",
        )
    ],
    transition="ログイン → SCR-001",
    integrations=[],
    data_io="なし",
    apis=[
        GeneratedApi(
            method="POST",
            path="/api/v1/reservations",
            summary="予約を登録する",
            screens=["SCR-001"],
            req_ids=["FR-001"],
            body="item_id, start_at",
            response="201 予約",
            errors="409 重複",
        ),
        GeneratedApi(
            method="GET",
            path="/api/v1/reservations",
            summary="予約の一覧を返す",
            screens=["SCR-001"],
            req_ids=["FR-002"],
            response="200 予約の配列",
        ),
    ],
)

# 構造化出力で返す固定の出力(スキーマの型ごと)。構成図は段階4がそのまま使う。
_UML_OUTPUTS: dict[type[BaseModel], BaseModel] = {
    ComponentGenerationOutput: ComponentGenerationOutput(
        modules=[
            GeneratedModule(id="m1", name="api", description="[E2E Fake] ルーター", layer="api"),
            GeneratedModule(
                id="m2", name="service", description="[E2E Fake] ユースケース", layer="service"
            ),
        ],
        dependencies=[GeneratedDependency(id="d1", source_id="m1", target_id="m2")],
    ),
}

# 詳細設計モードの段階1(機能一覧)の下書き。_EXTERNAL_DESIGN_OUTPUT の API 一覧と同じAPI。
# 要件ID は書かない(API の処理は、外部設計書の model の 2.6 の行から写す)
_UML_OUTPUTS[FunctionListGenerationOutput] = FunctionListGenerationOutput(
    functions=[
        GeneratedFunction(
            name="予約を登録する",
            kind="API",
            trigger="POST /api/v1/reservations",
            screens=["SCR-001"],
            summary="[E2E Fake] 備品と期間を検証して予約を保存する",
        ),
        GeneratedFunction(
            name="予約の一覧を返す",
            kind="API",
            trigger="GET /api/v1/reservations",
            screens=["SCR-001"],
            summary="[E2E Fake] 利用者の予約を返す",
        ),
    ]
)

# 詳細設計モードの段階2(データフロー)の下書き。段階1の下書きの2処理(F-01・F-02、
# 機能グループ reservations)に対応する
_UML_OUTPUTS[ProcessSummaryGenerationOutput] = ProcessSummaryGenerationOutput(
    rows=[
        GeneratedSummary(
            function_id="F-01",
            input="予約リクエスト",
            process="[E2E Fake] 備品と期間を検証して保存する",
            output="予約",
        ),
        GeneratedSummary(
            function_id="F-02",
            input="利用者",
            process="[E2E Fake] 利用者の予約を新しい順に返す",
            output="予約の一覧",
        ),
    ]
)
_UML_OUTPUTS[GroupDfdGenerationOutput] = GroupDfdGenerationOutput(
    data_items=[
        GeneratedDataItem(
            name="予約リクエスト", fields=[GeneratedDataItemField(name="item_id", type="UUID")]
        ),
        GeneratedDataItem(name="予約", fields=[GeneratedDataItemField(name="id", type="UUID")]),
    ],
    processes=[
        GeneratedGroupProcess(
            function_id="F-01", description="[E2E Fake] 検証して保存", layer="受け付け"
        ),
        GeneratedGroupProcess(
            function_id="F-02", description="[E2E Fake] 予約を返す", layer="参照"
        ),
    ],
    external_entities=[GeneratedNode(id="e1", name="利用者")],
    data_stores=[GeneratedNode(id="s1", name="reservations")],
    flows=[
        GeneratedFlow(id="f1", source_id="e1", target_id="F-01", data_item_name="予約リクエスト"),
        GeneratedFlow(id="f2", source_id="F-01", target_id="s1", data_item_name="予約"),
        GeneratedFlow(id="f3", source_id="s1", target_id="F-02", data_item_name="予約"),
        GeneratedFlow(id="f4", source_id="F-02", target_id="e1", data_item_name="予約"),
    ],
)

# 詳細設計モードの段階3(データモデル)の下書き。段階2の下書きの DFD のデータストア
# reservations(F-01 が書き、F-02 が読む)に対応する
_UML_OUTPUTS[DataModelErOutput] = DataModelErOutput(
    tables=[
        DraftedTable(
            id="t1",
            name="reservations",
            description="[E2E Fake] 備品の予約",
            columns=[
                DraftedColumn(
                    name="id",
                    type="UUID",
                    is_primary_key=True,
                    is_foreign_key=False,
                    nullable=False,
                    description="予約ID",
                ),
                DraftedColumn(
                    name="item_id",
                    type="UUID",
                    is_primary_key=False,
                    is_foreign_key=False,
                    nullable=False,
                    constraints="INDEX",
                    description="予約する備品",
                ),
            ],
        )
    ],
    relations=[],
)
_UML_OUTPUTS[CrudGenerationOutput] = CrudGenerationOutput(
    cells=[
        GeneratedCrudCell(function_id="F-01", table="reservations", ops="C"),
        GeneratedCrudCell(function_id="F-02", table="reservations", ops="R"),
    ]
)

# 詳細設計モードの段階4(ソフトウェア構造)のモジュール一覧。構成図は上の
# ComponentGenerationOutput(層 api・service)をそのまま使い、その層にそろえる
_UML_OUTPUTS[ModuleListGenerationOutput] = ModuleListGenerationOutput(
    modules=[
        GeneratedModuleRow(
            path="app/main.py",
            layer="api",
            responsibility="[E2E Fake] アプリの組み立て",
            depends_on=["app/api/routes/reservations.py"],
            functions=[],
            all_functions=True,
        ),
        GeneratedModuleRow(
            path="app/api/routes/reservations.py",
            layer="api",
            responsibility="[E2E Fake] 予約の API",
            depends_on=["app/services/reservation.py"],
            functions=["F-01", "F-02"],
            all_functions=False,
        ),
        GeneratedModuleRow(
            path="app/services/reservation.py",
            layer="service",
            responsibility="[E2E Fake] 予約の登録と一覧",
            depends_on=["sqlalchemy"],
            functions=["F-01", "F-02"],
            all_functions=False,
        ),
    ]
)

# 詳細設計モードの段階5(主要処理の手順)の手順。どの処理にも同じ手順を返す。
# 呼び出し先は、上のモジュール一覧のパスにそろえる
_UML_OUTPUTS[ProcedureGenerationOutput] = ProcedureGenerationOutput(
    reason="[E2E Fake] 予約の重複を防ぐ確認がある",
    note="[E2E Fake] 手順 2〜3 が1つのトランザクション",
    steps=[
        GeneratedStep(
            caller="利用者",
            callee="app/api/routes/reservations.py",
            call="create_reservation",
            data="予約リクエスト",
            action="本文を型で検証する",
            result="ReservationCreate",
            db="—",
            branches=[GeneratedBranch(condition="本文の型が不正", result="422")],
        ),
        GeneratedStep(
            caller="app/api/routes/reservations.py",
            callee="app/services/reservation.py",
            call="ReservationService.create",
            data="予約リクエスト",
            action="重複を確かめて予約を保存する",
            result="予約",
            db="reservations C",
        ),
        GeneratedStep(
            caller="app/api/routes/reservations.py",
            callee="利用者",
            call="",
            data="予約",
            action="応答に詰めて返す",
            result="201 Created",
            db="—",
            kind="return",
        ),
    ],
)

# 詳細設計モードの段階6(処理ロジックの詳細)の関数の詳細。どの関数にも同じ詳細を返す
_UML_OUTPUTS[LogicGenerationOutput] = LogicGenerationOutput(
    signature="async def create(self, payload: ReservationCreate) -> Reservation",
    args="payload: 予約リクエスト",
    returns="保存済みの予約",
    raises="ReservationConflictError(409)",
    pre="[E2E Fake] 利用者は認証済み",
    post="[E2E Fake] 予約が1件増える",
    pseudo=[
        GeneratedPseudoStep(text="期間が重なる予約を数える", sub=["重なりがあれば 409"]),
        GeneratedPseudoStep(text="予約を保存して返す", sub=[]),
    ],
)

# 詳細設計モードの段階7(横断事項と実装計画)の下書き。段階1の2処理(F-01・F-02)と、
# 段階4のモジュール一覧のパスにそろえる。基盤の単位 M-01-T01 の後に、処理ごとの機能の単位を置く
_UML_OUTPUTS[CrossCuttingGenerationOutput] = CrossCuttingGenerationOutput(
    crosscutting=[
        GeneratedCrossCutting(
            topic=topic, policy=f"[E2E Fake] {topic}の方針", modules=["app/main.py"]
        )
        for topic in ("例外と HTTP", "認証", "トランザクション", "ログ")
    ]
)
_UML_OUTPUTS[PlanGenerationOutput] = PlanGenerationOutput(
    milestones=[
        GeneratedMilestone(
            name="[E2E Fake] 予約の登録と一覧",
            goal="予約を登録して一覧で確かめられる",
            priority="Must",
            tasks=[
                GeneratedTask(
                    kind="base",
                    title="開発環境を用意する",
                    function_ids=[],
                    depends_on=[],
                    modules=[],
                    config_files=[
                        "Dockerfile",
                        "docker-compose.yml",
                        "alembic/versions/0001_reservations.py",
                    ],
                ),
                GeneratedTask(
                    kind="feature",
                    title="予約を登録する",
                    function_ids=["F-01"],
                    depends_on=["M-01-T01"],
                    modules=["app/api/routes/reservations.py", "app/services/reservation.py"],
                    config_files=[],
                ),
                GeneratedTask(
                    kind="feature",
                    title="予約の一覧を見る",
                    function_ids=["F-02"],
                    depends_on=["M-01-T02"],
                    modules=["app/api/routes/reservations.py", "app/services/reservation.py"],
                    config_files=[],
                ),
            ],
        )
    ],
    environment="[E2E Fake] Python 3.13・PostgreSQL・GitHub Actions",
    risks=[GeneratedRisk(risk="[E2E Fake] 予約の重複", mitigation="一意制約で防ぐ")],
)

# 段階8(実装手順書)の手順書。どの単位にも同じ手順書を返す。ファイルは段階4のモジュール一覧
# (詳細設計モード)・内部設計書のモジュール一覧(簡易モード)のパスにそろえる。指摘は軽微な1件だけに
# する(最重要があると、画面で承認の前に確認が挟まるため)。
_PROCEDURE_DOC_TESTS = [
    GeneratedTestPoint(
        viewpoint="[E2E Fake] 予約を登録すると一覧に出る",
        sut="POST /api/v1/reservations",
        driver="結合テスト(HTTP クライアントで呼ぶ)",
        stub="スタブ不要 ── テスト用の DB を使う",
    )
]
_UML_OUTPUTS[ProcedureDocGenerationOutput] = ProcedureDocGenerationOutput(
    purpose="[E2E Fake] 予約を登録して一覧で確かめられる",
    files=[
        GeneratedUnitFile(
            path="app/services/reservation.py",
            kind="module",
            responsibility="[E2E Fake] 予約の登録と一覧",
            basis="段階4",
        ),
        GeneratedUnitFile(
            path="app/api/routes/reservations.py",
            kind="module",
            responsibility="[E2E Fake] 予約の API",
            basis="段階4",
        ),
        GeneratedUnitFile(
            path="tests/test_reservations.py",
            kind="test",
            responsibility="[E2E Fake] 予約の登録と一覧のテスト",
            basis="テスト観点",
        ),
    ],
    notes=["[E2E Fake] サービスを先に書き、ルートから呼ぶ"],
    tests=_PROCEDURE_DOC_TESTS,
    gwt=["[E2E Fake] Given 予約が無い / When 登録する / Then 一覧に1件出る"],
    verify=["[E2E Fake] テストが通る"],
    findings=[
        GeneratedFinding(
            level="minor",
            target="07章 ログ",
            message="[E2E Fake] 登録のログの項目が決まっていない",
            fix_stage=7,
        )
    ],
)
_UML_OUTPUTS[SimpleProcedureDocGenerationOutput] = SimpleProcedureDocGenerationOutput(
    purpose="[E2E Fake] 予約を登録できる",
    files=[
        GeneratedUnitFile(
            path="app/services/reservation.py",
            kind="module",
            responsibility="[E2E Fake] 予約の検証と保存",
            basis="内部設計書 3.3",
        ),
        GeneratedUnitFile(
            path="app/api/reservations.py",
            kind="module",
            responsibility="[E2E Fake] 予約のルート",
            basis="内部設計書 3.3",
        ),
        GeneratedUnitFile(
            path="tests/test_reservations.py",
            kind="test",
            responsibility="[E2E Fake] 予約の登録のテスト",
            basis="テスト観点",
        ),
    ],
    notes=["[E2E Fake] サービスを先に書き、ルートから呼ぶ"],
    tests=_PROCEDURE_DOC_TESTS,
    gwt=["[E2E Fake] Given 予約が無い / When 登録する / Then 1件保存される"],
    verify=["[E2E Fake] テストが通る"],
    findings=[
        GeneratedSimpleFinding(
            level="minor",
            target="3.4",
            message="[E2E Fake] 登録のログの項目が決まっていない",
            fix_document="internal_design",
        )
    ],
)

# 設計レビューの偽物(ヒアリングが原因の指摘を1件含め、観点のキーの受け渡しを試せるようにする)
_REVIEW_OUTPUT = ReviewOutput(
    findings=[
        ReviewFindingOut(
            level="critical",
            cause="design",
            targets=["[E2E Fake] SCR-001"],
            documents=["external_design", "internal_design"],
            point_key=None,
            problem="[E2E Fake] 予約の取り消しの API が無い",
            evidence="[E2E Fake] 予約を取り消せる",
            reason="[E2E Fake] 画面の操作に API が無い",
            suggestion="[E2E Fake] 取り消しの API を足す",
        ),
        ReviewFindingOut(
            level="major",
            cause="hearing",
            targets=["[E2E Fake] 観点 セキュリティ"],
            documents=["requirements", "external_design"],
            point_key="security",
            problem="[E2E Fake] 誰が誰の予約を見られるかが決まっていない",
            evidence="[E2E Fake] 通信を HTTPS で暗号化する",
            reason="[E2E Fake] 要件にアクセス制御が無い",
            suggestion="[E2E Fake] 本人以外の閲覧の可否を聞き直す",
        ),
    ]
)

# 採用した指摘が直ったかの判定の偽物
_FIX_CHECK_OUTPUT = FixCheckOut(verdict="fixed", reason="[E2E Fake] 指摘の対象が直っている")

_HEARING_REPLY = "[E2E Fake] 承知しました。次に、想定している主なユーザー層を教えてください。"
_SELF_DIAGNOSIS_REPLY = "[E2E Fake] 自己診断: 特に致命的な不足点はありません。"


class _FakeChunk:
    """`astream()`が返すストリーミングチャンクを模した最小オブジェクト(`.content`のみ持つ)。"""

    def __init__(self, content: str) -> None:
        self.content = content


def _judge_points(messages: list[Any]) -> HearingPointsJudgement:
    """観点の判定の偽物。発話が足りるまでは目的だけ、足りたら全観点を「ユーザーが決定」で返す。

    末尾の1件は常にchat_service.judge_pointsが追記する判定プロンプト自身なので数えない。
    根拠は、番号の付いた発言のうち最後のもの(番号が無ければ1)。
    """
    conversation = [m for m in messages[:-1] if isinstance(m, HumanMessage)]
    numbers = [
        int(match.group(1))
        for m in conversation
        if (match := _MESSAGE_NUMBER.match(str(m.content))) is not None
    ]
    evidence = [max(numbers)] if numbers else [1]
    if len(conversation) < _TURNS_UNTIL_SUFFICIENT:
        return HearingPointsJudgement(
            points=[
                JudgedPointOut(
                    key="purpose", status="decided", summary="[E2E Fake] 目的", evidence=evidence
                )
            ]
        )
    return HearingPointsJudgement(
        scale="personal",
        points=[
            JudgedPointOut(
                key=d.key, status="decided", summary=f"[E2E Fake] {d.label}", evidence=evidence
            )
            for d in POINT_DEFS
        ],
    )


def _hearing_reply(messages: list[Any]) -> HearingReplyOut:
    """ヒアリングの返信の偽物。固定の本文と、カードにしてよい観点の先頭1つのカードを返す
    (候補が無ければカードは無し。規模のカードはchat_serviceが出すので、ここでは作らない)。"""
    system_text = "\n".join(
        str(m.content) for m in messages if m.__class__.__name__ == "SystemMessage"
    )
    keys = [key for key in _CARD_TARGET.findall(system_text) if is_point_key(key)]
    cards = [
        CardOut(
            point_key=keys[0],  # type: ignore[arg-type]
            question="[E2E Fake] どれにしますか",
            options=[
                CardOptionOut(label="[E2E Fake] 小さく始める", result="手間が少ない"),
                CardOptionOut(label="[E2E Fake] しっかり作る", result="手間が増える"),
            ],
            recommended=0,
            reason="[E2E Fake] 最初の版のため",
        )
    ] if keys else []
    return HearingReplyOut(message=_HEARING_REPLY, cards=cards)


# 要件定義書の偽物の要件(種別・優先度・大項目・本文・出どころ・観点)。出どころは
# doc_generator_service が観点の表で上限をかける。推測の要件を1つ含め、プレビューの
# 「採用にする」を E2E で試せるようにする。規模だけを根拠にした制約は、規模の状態が上限になる。
_REQUIREMENTS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("FR", "Must", "", "[E2E Fake] 備品を予約できる", "user", "core"),
    ("FR", "Should", "", "[E2E Fake] 予約の履歴を見られる", "user", "future"),
    ("NFR", "", "可用性", "[E2E Fake] 半日止まってもよい", "adopted", "availability"),
    ("NFR", "", "セキュリティ", "[E2E Fake] 通信を HTTPS で暗号化する", "assumed", "security"),
    ("CON", "", "技術スタック", "[E2E Fake] Python と PostgreSQL を使う", "user", "tech"),
    ("CON", "", "規模", "[E2E Fake] 個人で使う", "user", "scale"),
)
_PREVIOUS_SECTION = "## 前の版の要件\n"


def _previous_ids(messages: list[Any]) -> dict[str, str]:
    """プロンプトの【前の版の要件】(`ID|本文`の行)から、本文 → ID を読む(ID の引き継ぎの契約)。"""
    human = "\n".join(str(m.content) for m in messages if isinstance(m, HumanMessage))
    if _PREVIOUS_SECTION not in human:
        return {}
    section = human.split(_PREVIOUS_SECTION, 1)[1].split("\n\n", 1)[0]
    pairs = [line.split("|", 1) for line in section.splitlines() if "|" in line]
    return {text: req_id for req_id, text in pairs}


def _requirements_output(messages: list[Any]) -> RequirementsGenerationOutput:
    """要件定義書の下書きの偽物。前の版に同じ本文の要件があれば、その ID を prev_id に返す。"""
    previous = _previous_ids(messages)
    return RequirementsGenerationOutput(
        purpose="[E2E Fake] 備品の予約を楽にする",
        users=[GeneratedUserScene(user="[E2E Fake] 社員", scene="備品を借りるとき", source="user")],
        in_scope=["[E2E Fake] 備品の予約"],
        out_of_scope=["[E2E Fake] 決済"],
        requirements=[
            GeneratedRequirement(
                kind=kind,  # type: ignore[arg-type]
                prev_id=previous.get(text, ""),
                priority=priority,  # type: ignore[arg-type]
                category=category,
                text=text,
                source=source,  # type: ignore[arg-type]
                points=[point],
            )
            for kind, priority, category, text, source, point in _REQUIREMENTS
        ],
    )


class _FakeStructuredE2e:
    """`with_structured_output(schema)`が返す構造化出力用サブクライアントのE2E版。"""

    def __init__(self, schema: type[BaseModel], *, include_raw: bool = False) -> None:
        self._schema = schema
        self._include_raw = include_raw

    async def ainvoke(self, messages: list[Any]) -> Any:
        parsed = self._parsed_for(messages)
        if not self._include_raw:
            return parsed
        # with_structured_output(schema, include_raw=True)の実物と同じ形で返す
        raw = AIMessage(content="", response_metadata={"finish_reason": "STOP"})
        return {"raw": raw, "parsed": parsed, "parsing_error": None}

    def _parsed_for(self, messages: list[Any]) -> BaseModel:
        if self._schema in _UML_OUTPUTS:
            return _UML_OUTPUTS[self._schema]
        if self._schema is HearingPointsJudgement:
            return _judge_points(messages)
        if self._schema is HearingReplyOut:
            return _hearing_reply(messages)
        if self._schema is RequirementsGenerationOutput:
            return _requirements_output(messages)
        if self._schema is ExternalDesignGenerationOutput:
            return _EXTERNAL_DESIGN_OUTPUT
        if self._schema is internal_doc.InternalDesignGenerationOutput:
            return _INTERNAL_DESIGN_OUTPUT
        if self._schema is plan_doc.ImplementationPlanGenerationOutput:
            return _IMPLEMENTATION_PLAN_OUTPUT
        if self._schema is ReviewOutput:
            return _REVIEW_OUTPUT
        if self._schema is FixCheckOut:
            return _FIX_CHECK_OUTPUT
        # ヒアリング・UML生成スキーマ以外のスキーマが追加された場合は
        # 「対応漏れ」に気づけるよう黙って汎用値を返さず例外にする。
        raise NotImplementedError(f"E2eFakeLLMが未対応のスキーマ: {self._schema}")


class E2eFakeLLM:
    """`E2E_FAKE_LLM=true`のときget_gemini_llm()・get_review_llm()が返すスタブ実装。

    chat_service.py・doc_generator_service.pyが実際に呼び出すメソッド(astream/ainvoke/
    with_structured_output().ainvoke)のみを実装する(invoke()の同期版は呼ばれないため
    実装しない ── 呼ばれないメソッドまで用意すると「対応済みに見えて実は未検証」の箇所が
    増えるため、CLAUDE.md #17の精神で必要な分だけに絞る)。
    """

    async def astream(self, messages: list[Any]) -> AsyncIterator[_FakeChunk]:
        yield _FakeChunk(self._reply_for(messages))

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return AIMessage(content=self._reply_for(messages))

    def with_structured_output(
        self, schema: type[BaseModel], *, include_raw: bool = False, method: str | None = None
    ) -> _FakeStructuredE2e:
        # method(構造化出力の方式。設計レビューが "json_schema" を渡す)は偽物では使わない
        return _FakeStructuredE2e(schema, include_raw=include_raw)

    def _reply_for(self, messages: list[Any]) -> str:
        system_text = "\n".join(
            str(m.content) for m in messages if m.__class__.__name__ == "SystemMessage"
        )
        if "レビュアー" in system_text:
            return _SELF_DIAGNOSIS_REPLY
        # ヒアリング対話(_HEARING_SYSTEM_PROMPT、_OPENING_TURN_PROMPT共通)への応答。
        # 内容の質はE2Eの検証対象ではない(UI遷移・状態遷移を検証する)ため、
        # 固定文言で十分とする。
        return _HEARING_REPLY


@lru_cache
def get_e2e_fake_llm() -> E2eFakeLLM:
    """E2eFakeLLMインスタンスをプロセス内で1つだけ生成する(get_gemini_llmと同じキャッシュ方針)。"""
    return E2eFakeLLM()
