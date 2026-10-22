"""変更要求(CR)の選択肢(案)の整え方・保存の形・案ごとの規模のテスト。

SUT: app.change_requests の normalize_options / option_target_problems / options_to_json /
     options_from_json / options_from_rows / option_scales / option_label と、ChangeOption・
     ChangeOptions・OptionScale
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため。規模の入力は、段階1〜8 を持つ
fixture の model を束の写しの形に並べたもの(test_change_request_scale.py と同じ)
"""

import pytest
from tests.fixtures.change_impact import impact_documents, impact_stages

from app.change_requests import (
    OPTIONS_MAX,
    ChangeOption,
    ChangeOptions,
    OptionScale,
    Target,
    normalize_options,
    option_label,
    option_scales,
    option_target_problems,
    options_from_json,
    options_from_rows,
    options_to_json,
)


def _snapshots() -> dict[str, dict]:
    stages = {f"stage:{n}": model for n, model in impact_stages().items()}
    return {**impact_documents(), **stages}


def _two_options(**kwargs) -> ChangeOptions:
    return options_from_rows(
        [
            (" メールだけ ", [Target("FR-001", "content", "確認メールを送る")], " 手間が小さい "),
            ("メールと画面", [Target("FR-001"), Target("FR-002")], "画面も直す"),
        ],
        **kwargs,
    )


def test_normalize_strips_and_keeps_order() -> None:
    """名前の空白をまとめ、結果・理由の前後の空白を除く。案の順と対象の順はそのまま。"""
    options = normalize_options(_two_options(recommended=1, reason=" 早く出せる "))

    assert [o.name for o in options.items] == ["メールだけ", "メールと画面"]
    assert options.items[0].outcome == "手間が小さい"
    assert options.items[1].targets == (Target("FR-001"), Target("FR-002"))
    assert options.recommended == 1
    assert options.reason == "早く出せる"
    assert options.option(2).name == "メールと画面"
    assert option_label(2) == "案2"


def test_no_options_is_empty() -> None:
    """案の無い CR は空(おすすめも無い)。"""
    assert normalize_options(ChangeOptions()).empty
    with pytest.raises(ValueError, match="おすすめ"):
        normalize_options(ChangeOptions(recommended=1, reason="x"))


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (options_from_rows([("A", [Target("FR-001")], "x")]), "2〜3件"),
        (
            options_from_rows(
                [(f"案{i}", [Target("FR-001")], "x") for i in range(OPTIONS_MAX + 1)]
            ),
            "2〜3件",
        ),
        (options_from_rows([("A", [], "x"), ("A", [], "y")]), "重なって"),
        (options_from_rows([("A", [], ""), ("B", [], "y")]), "名前と結果"),
        (_two_options(recommended=3, reason="x"), "範囲の外"),
        (_two_options(recommended=1), "理由"),
        (_two_options(reason="x"), "選ばずに"),
        (
            options_from_rows(
                [("A", [Target("FR-001", "delete", "案")], "x"), ("B", [], "y")]
            ),
            "削除",
        ),
    ],
)
def test_normalize_rejects(options: ChangeOptions, message: str) -> None:
    """案の数・名前・結果・おすすめ・対象の規則に合わなければ ValueError。"""
    with pytest.raises(ValueError, match=message):
        normalize_options(options)


def test_option_targets_must_be_within_the_change_request() -> None:
    """案の対象は CR の対象の中だけ。対象の無い案は、新しい要件を足す CR だけ。"""
    options = options_from_rows(
        [("A", [Target("FR-001"), Target("FR-009")], "x"), ("B", [], "y")]
    )

    problems = option_target_problems(options, [Target("FR-001")], adds_requirement=False)
    assert problems == [
        "案1の対象が CR の対象にありません: FR-009",
        "案2に変える対象がありません",
    ]
    assert option_target_problems(
        options, [Target("FR-001"), Target("FR-009")], adds_requirement=True
    ) == []


def test_json_round_trip() -> None:
    """保存の形から読み戻すと同じ案になる。案が無ければ None で保存し、None は案なしで読む。"""
    options = normalize_options(_two_options(recommended=2, reason="漏れが無い"))

    saved = options_to_json(options)
    assert saved is not None
    assert saved["items"][0]["targets"] == [
        {"requirement_id": "FR-001", "kind": "content", "proposed_text": "確認メールを送る"}
    ]
    assert options_from_json(saved) == options
    assert options_to_json(ChangeOptions()) is None
    assert options_from_json(None).empty


def test_option_scales_count_each_option_with_its_targets() -> None:
    """規模は案ごとに案の対象で数える(対象の多い案は作業単位が多い)。おすすめの印を付ける。"""
    options = normalize_options(_two_options(recommended=1, reason="早い"))

    scales = option_scales(options, _snapshots(), {}, adds_requirement=False)

    assert [(s.number, s.name, s.recommended) for s in scales] == [
        (1, "メールだけ", True),
        (2, "メールと画面", False),
    ]
    assert isinstance(scales[0], OptionScale)
    first, second = (s.scale.of("unit").count for s in scales)
    assert first is not None and second is not None
    assert first < second


def test_option_without_targets_uses_the_shared_origin() -> None:
    """対象の無い案(新しい要件を足すだけ)は、CR に共通の起点で予測する。起点が無ければ
    数えられない。"""
    options = ChangeOptions((ChangeOption("足すだけ", (), "x"), ChangeOption("B", (), "y")))

    without_origin = option_scales(options, _snapshots(), {}, adds_requirement=True)
    assert without_origin[0].scale.of("unit").count is None
