"""システムの既定のモデル(`.env` の `GEMINI_MODEL`・`REVIEW_MODEL`・`JUDGE_MODEL`)を、
用途に割り当てる。

seed が既定値の行を書くときと、既定値の行がまだ無い DB で画面に既定を出すときに使う。
"""

from app.ai.llm.catalog import Provider, Purpose, provider_of
from app.core.config import Settings


def env_default_models(settings: Settings) -> dict[Purpose, tuple[Provider, str]]:
    """用途ごとの (provider, モデル)。チャットと生成は同じ `GEMINI_MODEL` を使う。

    `.env` のモデルが選べるモデルの表に無ければ ValueError(既定値をカタログの外にしない)。
    """
    models = {
        Purpose.CHAT: settings.GEMINI_MODEL,
        Purpose.GENERATION: settings.GEMINI_MODEL,
        Purpose.REVIEW: settings.REVIEW_MODEL,
        Purpose.JUDGE: settings.JUDGE_MODEL,
    }
    defaults: dict[Purpose, tuple[Provider, str]] = {}
    for purpose, model in models.items():
        provider = provider_of(model)
        if provider is None:
            raise ValueError(f"既定のモデル {model}({purpose})が、選べるモデルの表にありません。")
        defaults[purpose] = (provider, model)
    return defaults
