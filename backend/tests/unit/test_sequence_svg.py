"""シーケンス図の SVG の書き出しのテスト。

SUT: to_sequence_svg / message_label(app/detailed_design/sequence_svg.py)
ドライバ: 各テスト関数
スタブ不要 ── 純粋関数(副作用なし)で、外部依存を呼ばないため。図のモデルは`to_sequence`で
手作りの手順から作る(導出のテストは test_sequence.py)。
"""

import re

from app.detailed_design import Procedure, ProcedureStep, SequenceMessage, to_sequence
from app.detailed_design.sequence_svg import message_label, to_sequence_svg

ROUTE = "a/route.py"
SERVICE = "a/service.py"


def _diagram():
    return to_sequence(
        Procedure(
            function_id="F-01",
            steps=[
                ProcedureStep(caller="利用者", callee=ROUTE, call="post", data="<本文>"),
                ProcedureStep(action="不正", branch="422", is_branch=True),
                ProcedureStep(caller=ROUTE, callee=SERVICE, call="run", result="結果"),
                ProcedureStep(caller=SERVICE, callee=SERVICE, call="_inner"),
                ProcedureStep(caller=SERVICE, callee=ROUTE, kind="async", call="notify"),
            ],
        )
    )


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_svg_has_participants_arrows_and_note():
    svg = to_sequence_svg(_diagram())

    assert svg.startswith("<svg ") and svg.endswith("</svg>")
    assert svg.count('stroke="#4a6fa5"') == 3  # 参加者の箱
    assert "1a 不正 → 422" in svg  # 分岐の注記


def test_svg_is_deterministic_and_escapes_text():
    svg = to_sequence_svg(_diagram())

    assert svg == to_sequence_svg(_diagram())
    assert "&lt;本文&gt;" in svg and "<本文>" not in svg


def test_arrow_styles_follow_kind_and_derived_returns_are_italic():
    svg = to_sequence_svg(_diagram())

    assert svg.count('marker-end="url(#sq-call)"') == 3  # 1・2(同期)と 3(自己呼び出し)
    assert svg.count('marker-end="url(#sq-open)"') == 3  # 4(非同期)と推測した戻り2本
    assert svg.count('stroke-dasharray="6,4"') == 2  # 戻りは破線
    assert svg.count('font-style="italic"') == 2  # 推測した戻りのラベル


def test_message_label_has_step_number_and_is_clipped():
    events = [e for e in _diagram().events if isinstance(e, SequenceMessage)]
    assert message_label(events[0]) == "1: post(<本文>)"
    assert message_label(events[-1]).startswith("(1 の戻り)")
    long = SequenceMessage("F-01#9", "P1", "P2", "call", "x" * 80)
    assert message_label(long).endswith("…") and len(message_label(long)) == 48


def test_columns_widen_for_long_labels():
    short = to_sequence(
        Procedure(function_id="F-01", steps=[ProcedureStep(caller="a", callee="b", call="f")])
    )
    long = to_sequence(
        Procedure(
            function_id="F-01", steps=[ProcedureStep(caller="a", callee="b", call="f" * 40)]
        )
    )

    def width(svg: str) -> float:
        match = re.search(r'width="([0-9.]+)"', svg)
        assert match is not None
        return float(match.group(1))

    assert width(to_sequence_svg(long)) > width(to_sequence_svg(short))
    assert to_sequence_svg(to_sequence(Procedure(function_id="F-01"))).startswith("<svg ")
