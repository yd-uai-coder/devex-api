"""LLM クライアントの挙動を模したテスト用スタブ。

`with_structured_output(schema)` の呼び出しごとに `schema` を `structured_output_calls` に
記録する ── どの用途でどのスキーマが使われたかの検証で使う。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from pydantic import BaseModel


class FakeLLM:
    """LLM クライアント(`get_gemini_llm` などの戻り値)の挙動を模したスタブ。"""

    def __init__(
        self,
        content: str | None = None,
        structured: BaseModel | None = None,
        structured_sequence: list[BaseModel | Exception] | None = None,
    ) -> None:
        # content: invoke()が返すAIMessageの本文
        # structured: with_structured_output().invoke()が返す構造化レスポンス(固定1件)
        # structured_sequence: 呼び出しごとに1つずつ消費する構造化レスポンス/例外の列
        self._content = content
        self._structured = structured
        self._structured_sequence = structured_sequence
        self.structured_output_calls: list[type[BaseModel]] = []

    def invoke(self, _messages: Any) -> AIMessage:
        """通常のinvoke呼び出しの結果としてAIMessageを返す。"""
        return AIMessage(content=self._content)

    async def ainvoke(self, messages: Any) -> AIMessage:
        """invoke の非同期版(結果は同じ)。"""
        return self.invoke(messages)

    def with_structured_output(self, schema: type[BaseModel]) -> _FakeStructuredLLM:
        """構造化出力用のサブクライアントを返す。呼ばれた schema を記録する。"""
        self.structured_output_calls.append(schema)
        return _FakeStructuredLLM(self._structured, self._structured_sequence)


class _FakeStructuredLLM:
    """with_structured_output()が返す、構造化レスポンスのみを返すテスト用スタブ。"""

    def __init__(
        self,
        structured: BaseModel | None,
        sequence: list[BaseModel | Exception] | None = None,
    ) -> None:
        self._structured = structured
        self._sequence = sequence

    def invoke(self, _messages: Any) -> BaseModel | None:
        """構造化済みレスポンスをそのまま返す。sequence 指定時は先頭から1つずつ消費し、
        値が Exception インスタンスならその回の呼び出しとして送出する。"""
        if self._sequence is not None:
            item = self._sequence.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return self._structured

    async def ainvoke(self, messages: Any) -> BaseModel | None:
        """invoke の非同期版(結果は同じ)。"""
        return self.invoke(messages)
