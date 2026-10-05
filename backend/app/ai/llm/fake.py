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
from app.detailed_design.procedure_drafting import GeneratedStep, ProcedureGenerationOutput
from app.detailed_design.structure_drafting import GeneratedModuleRow, ModuleListGenerationOutput
from app.schemas.generation import HearingCompletionCheck
from app.uml.generation.schemas import (
    ComponentGenerationOutput,
    GeneratedDataItem,
    GeneratedDataItemField,
    GeneratedDependency,
    GeneratedFlow,
    GeneratedModule,
    GeneratedNode,
)

# ヒアリング完了(is_sufficient=True)と判定するまでに要するHumanMessage数(末尾の判定プロンプト
# 自身を除く)。「初期ヒアリング入力(intake)1件+実際のチャット発話2件」で3件になる。
# chat_service.check_completionは末尾に_COMPLETION_CHECK_PROMPT自身のHumanMessageを
# 追記するため、これも数えてしまうと実際のチャット発話が0件でも条件を満たしてしまう。
_TURNS_UNTIL_SUFFICIENT = 4

# doc_generator_service.pyの各doc_type専用プロンプト(_DOC_TYPE_PROMPTS)は、他doc_typeへの
# 入力参照を「以下の【要件定義書】および【外部設計書】に基づき...」のような角括弧表記で行うため、
# 単純な「要件定義書」等の裸のラベル文字列でマッチさせると、internal_design/external_design/
# implementation_plan向けのプロンプト(要件定義書という文字列を必ず含む)が誤って
# requirementsと判定されてしまう。各プロンプトが自分自身の
# 出力フォーマットとして持つ「# N. ラベル」という見出し(cross-reference表記には現れない、
# doc_type固有の文字列)をマッチ対象にすることでこれを避ける。
_DOC_TYPE_MARKERS: dict[str, str] = {
    "requirements": "# 1. 要件定義書",
    "external_design": "# 2. 外部設計書",
    "internal_design": "# 3. 内部設計書",
    "implementation_plan": "# 4. 実装計画書",
}
_DOC_TYPE_LABELS: dict[str, str] = {
    "requirements": "要件定義書",
    "external_design": "外部設計書",
    "internal_design": "内部設計書",
    "implementation_plan": "実装計画書",
}

# 内部設計書は、内部設計書プロンプトが指示する固定形式の見出し(3.2節のテーブル・処理別データフロー)
# を含めて返す。
_INTERNAL_DESIGN_REPLY = (
    "# 内部設計書(E2E Fake)\n\n"
    "## 3.1 技術スタック選定・アーキテクチャ方針\n- api / service / repository の3層構成\n\n"
    "## 3.2 データモデル定義\n\n"
    "### テーブル: reservations\n| カラム名 | データ型 | 制約 | 説明 |\n|---|---|---|---|\n"
    "| id | UUID | PK | 予約ID |\n\n"
    "## 3.3 バックエンド処理・モジュール設計\n- api → service → repository\n\n"
    "### 処理別データフロー\n\n"
    "#### DF-1: POST /api/v1/reservations\n| 元 | データ | 変換 | 先 |\n|---|---|---|---|\n"
    "| 利用者 | 予約リクエスト | 検証して保存 | reservations |\n"
    "- データ項目: 予約リクエスト(item_id, start_at)\n"
)

# 外部設計書は、詳細設計モードの段階1(機能一覧)がAPI一覧(2.6節)を読むため、外部設計書
# プロンプトが指示する固定形式の表を含めて返す。
_EXTERNAL_DESIGN_REPLY = (
    "# 外部設計書(E2E Fake)\n\n"
    "## 2.2 画面一覧・画面遷移フロー（概要）\n| 画面ID | 画面名 | 主要な役割 | 優先度 |\n"
    "|---|---|---|---|\n| SCR-001 | 予約画面 | 備品を予約する | Must |\n\n"
    "## 2.6 API一覧\n| メソッド | パス | 概要 | 関連画面 |\n|---|---|---|---|\n"
    "| POST | /api/v1/reservations | 予約を登録する | SCR-001 |\n"
    "| GET | /api/v1/reservations | 予約の一覧を返す | SCR-001 |\n"
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

# 詳細設計モードの段階1(機能一覧)の下書き。_EXTERNAL_DESIGN_REPLY の API 一覧と同じAPI
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
            branch="1a へ",
            is_branch=False,
        ),
        GeneratedStep(
            caller="",
            callee="",
            call="",
            data="",
            action="本文の型が不正",
            result="",
            db="",
            branch="422",
            is_branch=True,
        ),
        GeneratedStep(
            caller="app/api/routes/reservations.py",
            callee="app/services/reservation.py",
            call="ReservationService.create",
            data="予約リクエスト",
            action="重複を確かめて予約を保存する",
            result="予約",
            db="reservations C",
            branch="—",
            is_branch=False,
        ),
        GeneratedStep(
            caller="app/api/routes/reservations.py",
            callee="利用者",
            call="",
            data="予約",
            action="応答に詰めて返す",
            result="201 Created",
            db="—",
            branch="—",
            is_branch=False,
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
# 段階4のモジュール一覧のパスにそろえる
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
            function_ids=["F-01", "F-02"],
            tasks=[
                GeneratedTask(
                    area="バックエンド",
                    title="予約の API とサービスを作る",
                    modules=["app/api/routes/reservations.py", "app/services/reservation.py"],
                    function_ids=["F-01", "F-02"],
                ),
            ],
        )
    ],
    environment="[E2E Fake] Python 3.13・PostgreSQL・GitHub Actions",
    risks=[GeneratedRisk(risk="[E2E Fake] 予約の重複", mitigation="一意制約で防ぐ")],
)

_HEARING_REPLY = "[E2E Fake] 承知しました。次に、想定している主なユーザー層を教えてください。"
_SELF_DIAGNOSIS_REPLY = "[E2E Fake] 自己診断: 特に致命的な不足点はありません。"


class _FakeChunk:
    """`astream()`が返すストリーミングチャンクを模した最小オブジェクト(`.content`のみ持つ)。"""

    def __init__(self, content: str) -> None:
        self.content = content


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
        if self._schema is HearingCompletionCheck:
            # 末尾の1件は常にchat_service.check_completionが追記する
            # _COMPLETION_CHECK_PROMPT自身のHumanMessageであり、実際の対話ターンではないため
            # 対象外にする。残りのHumanMessageは「初期ヒアリング入力(intake)1件+実際の
            # チャット発話N件」なので、_TURNS_UNTIL_SUFFICIENT=3は
            # 「intake+チャット2往復」を意味する。
            conversation_messages = messages[:-1]
            user_turns = sum(1 for m in conversation_messages if isinstance(m, HumanMessage))
            sufficient = user_turns >= _TURNS_UNTIL_SUFFICIENT
            return HearingCompletionCheck(
                is_sufficient=sufficient,
                summary=(
                    "[E2E Fake] ここまでのヒアリング内容を要約しました。"
                    "この内容で設計書を生成します。"
                    if sufficient
                    else "[E2E Fake] まだ確認したい点があります。"
                ),
                missing_points=[] if sufficient else ["[E2E Fake] 想定ユーザーの具体化"],
            )
        # HearingCompletionCheck・UML生成スキーマ以外のスキーマが追加された場合は
        # 「対応漏れ」に気づけるよう黙って汎用値を返さず例外にする。
        raise NotImplementedError(f"E2eFakeLLMが未対応のスキーマ: {self._schema}")


class E2eFakeLLM:
    """`E2E_FAKE_LLM=true`のときget_gemini_llm()が返すスタブ実装。

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
        self, schema: type[BaseModel], *, include_raw: bool = False
    ) -> _FakeStructuredE2e:
        return _FakeStructuredE2e(schema, include_raw=include_raw)

    def _reply_for(self, messages: list[Any]) -> str:
        system_text = "\n".join(
            str(m.content) for m in messages if m.__class__.__name__ == "SystemMessage"
        )
        for doc_type, marker in _DOC_TYPE_MARKERS.items():
            if marker in system_text:
                if doc_type == "internal_design":
                    return _INTERNAL_DESIGN_REPLY
                if doc_type == "external_design":
                    return _EXTERNAL_DESIGN_REPLY
                label = _DOC_TYPE_LABELS[doc_type]
                return f"# {label}(E2E Fake)\n\nこれはE2Eテスト用に生成されたダミーの{label}です。"
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
