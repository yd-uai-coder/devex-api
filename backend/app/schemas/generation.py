from pydantic import BaseModel, Field


class FinalAnswer(BaseModel):
    """LLMに最終回答を構造化出力させるためのスキーマ。"""

    answer: str = Field(description="ユーザーの質問に対する最終的な回答本文")
