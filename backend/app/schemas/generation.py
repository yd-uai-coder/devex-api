from pydantic import BaseModel, Field


class FinalAnswer(BaseModel):
    """LLMに最終回答を構造化出力させるためのスキーマ。"""

    answer: str = Field(description="ユーザーの質問に対する最終的な回答本文")


class HearingCompletionCheck(BaseModel):
    """ヒアリング完了判定(docs/external_design.md 2.3節SCR-004の5条件)を
    LLMに構造化出力させるためのスキーマ。"""

    is_sufficient: bool = Field(description="5条件をすべて満たし、設計書生成に進める状態かどうか")
    summary: str = Field(
        description="ここまでのヒアリング内容を要約した文章。ユーザーへの確認提示に使う"
    )
    missing_points: list[str] = Field(
        default_factory=list, description="is_sufficientがfalseの場合、まだ不足している観点の一覧"
    )
