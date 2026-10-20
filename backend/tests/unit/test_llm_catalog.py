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


def test_catalog_models_per_provider() -> None:
    counts = {p: len([m for m in CATALOG if m.provider == p]) for p in Provider}
    assert counts == {Provider.GEMINI: 3, Provider.CLAUDE: 4}
    assert len({m.model_id for m in CATALOG}) == len(CATALOG)
    assert set(PROVIDER_LABELS) == set(Provider)


def test_find_model_requires_matching_provider() -> None:
    assert find_model(Provider.CLAUDE, "claude-haiku-5-5") is not None
    assert find_model(Provider.GEMINI, "claude-haiku-5-5") is None
    assert find_model(Provider.GEMINI, "gemini-2.5-flash") is None
    # 終了の告知で外した(設定はマイグレーションで後継の 3.5 Flash-Lite へ移した)
    assert find_model(Provider.GEMINI, "gemini-3.1-flash-lite") is None


def test_provider_of() -> None:
    assert provider_of("gemini-3.5-flash-lite") is Provider.GEMINI
    assert provider_of("claude-sonnet-5-5") is Provider.CLAUDE
    assert provider_of("unknown") is None


def test_purposes_cover_four_roles_and_judge_recommends_low_cost() -> None:
    assert [p.purpose for p in PURPOSES] == list(Purpose)
    low_cost = [p.purpose for p in PURPOSES if p.recommend_low_cost]
    assert low_cost == [Purpose.JUDGE]


def test_every_model_has_a_timeout() -> None:
    assert all(m.timeout_seconds > 0 for m in CATALOG)
    # 3.8 Flash は1回の呼び出しが最大約176秒かかった(Flash-Lite の30秒では収まらない)
    flash = find_model(Provider.GEMINI, "gemini-3.8-flash")
    assert flash is not None and flash.timeout_seconds > 176
