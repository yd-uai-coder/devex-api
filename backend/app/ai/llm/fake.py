"""ブラウザ経由のE2Eテスト(Phase 4-4)専用の決定論的LLMスタブ。

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

from app.schemas.generation import HearingCompletionCheck

# ヒアリング完了(is_sufficient=True)と判定するまでに要するHumanMessage数(末尾の判定プロンプト
# 自身を除く)。「初期ヒアリング入力(intake)1件+実際のチャット発話2件」で3件になる。
# 当初は単純に「HumanMessageが2件以上」としていたが、実機検証で以下2件の不具合が見つかり
# 修正した経緯がある(詳細はPhase-4-3.mdの「実機検証で発見した2件の不具合」参照)。
# (1) chat_service.check_completionは末尾に_COMPLETION_CHECK_PROMPT自身のHumanMessageを
#     追記するため、これも数えてしまうと実際のチャット発話が0件でも条件を満たしてしまう。
_TURNS_UNTIL_SUFFICIENT = 3

# doc_generator_service.pyの各doc_type専用プロンプト(_DOC_TYPE_PROMPTS)は、他doc_typeへの
# 入力参照を「以下の【要件定義書】および【外部設計書】に基づき...」のような角括弧表記で行うため、
# 単純な「要件定義書」等の裸のラベル文字列でマッチさせると、internal_design/external_design/
# implementation_plan向けのプロンプト(要件定義書という文字列を必ず含む)が誤って
# requirementsと判定されてしまう(実機検証で発見、(2)の不具合)。各プロンプトが自分自身の
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

_HEARING_REPLY = "[E2E Fake] 承知しました。次に、想定している主なユーザー層を教えてください。"
_SELF_DIAGNOSIS_REPLY = "[E2E Fake] 自己診断: 特に致命的な不足点はありません。"


class _FakeChunk:
    """`astream()`が返すストリーミングチャンクを模した最小オブジェクト(`.content`のみ持つ)。"""

    def __init__(self, content: str) -> None:
        self.content = content


class _FakeStructuredE2e:
    """`with_structured_output(schema)`が返す構造化出力用サブクライアントのE2E版。"""

    def __init__(self, schema: type[BaseModel]) -> None:
        self._schema = schema

    async def ainvoke(self, messages: list[Any]) -> BaseModel:
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
        # HearingCompletionCheck以外のスキーマは現時点でこのアプリ内に無く、追加された場合は
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

    def with_structured_output(self, schema: type[BaseModel]) -> _FakeStructuredE2e:
        return _FakeStructuredE2e(schema)

    def _reply_for(self, messages: list[Any]) -> str:
        system_text = "\n".join(
            str(m.content) for m in messages if m.__class__.__name__ == "SystemMessage"
        )
        for doc_type, marker in _DOC_TYPE_MARKERS.items():
            if marker in system_text:
                label = _DOC_TYPE_LABELS[doc_type]
                return f"# {label}(E2E Fake)\n\nこれはE2Eテスト用に生成されたダミーの{label}です。"
        if "レビュアー" in system_text:
            return _SELF_DIAGNOSIS_REPLY
        # ヒアリング対話(_HEARING_SYSTEM_PROMPT、_OPENING_TURN_PROMPT共通)への応答。
        # 内容の質はE2Eの検証対象ではない(UI遷移・状態遷移の検証がPhase 4-4の狙い)ため、
        # 固定文言で十分とする。
        return _HEARING_REPLY


@lru_cache
def get_e2e_fake_llm() -> E2eFakeLLM:
    """E2eFakeLLMインスタンスをプロセス内で1つだけ生成する(get_gemini_llmと同じキャッシュ方針)。"""
    return E2eFakeLLM()
