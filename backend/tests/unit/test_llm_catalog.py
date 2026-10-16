"""選べるモデルの表と用途の定義のテスト。

SUT: app/ai/llm/catalog.py の CATALOG・PURPOSES・find_model・provider_of
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

from app.ai.llm.catalog import (
    CATALOG,
    PROVIDER_LABELS,
    PURPOSES,
    Provider,
    Purpose,
    find_model,
    provider_of,
)


def test_catalog_has_four_models_per_provider() -> None:
    for provider in Provider:
        assert len([m for m in CATALOG if m.provider == provider]) == 4
    assert len({m.model_id for m in CATALOG}) == len(CATALOG)
    assert set(PROVIDER_LABELS) == set(Provider)


def test_find_model_requires_matching_provider() -> None:
    assert find_model(Provider.CLAUDE, "claude-haiku-5-5") is not None
    assert find_model(Provider.GEMINI, "claude-haiku-5-5") is None
    assert find_model(Provider.GEMINI, "gemini-2.5-flash") is None


def test_provider_of() -> None:
    assert provider_of("gemini-3.5-flash-lite") is Provider.GEMINI
    assert provider_of("claude-sonnet-5-5") is Provider.CLAUDE
    assert provider_of("unknown") is None


def test_purposes_cover_four_roles_and_judge_recommends_low_cost() -> None:
    assert [p.purpose for p in PURPOSES] == list(Purpose)
    low_cost = [p.purpose for p in PURPOSES if p.recommend_low_cost]
    assert low_cost == [Purpose.JUDGE]
