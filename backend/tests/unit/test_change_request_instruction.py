"""合意した CR からの再生成の指示のテスト。

SUT: app.change_requests(build_cr_instruction / join_instructions)
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

from datetime import date

from app.change_requests import (
    ChangeRequestFacts,
    Target,
    build_cr_instruction,
    join_instructions,
)
from app.change_requests.instruction import ADDS_REQUIREMENT_LINE


def _facts(targets: tuple[Target, ...], **overrides: object) -> ChangeRequestFacts:
    values: dict[str, object] = {
        "number": 3,
        "title": "参照ファイルを5つまでにする",
        "status": "agreed",
        "content": "添付できる参照ファイルを3つから5つに増やす",
        "background_source": "meeting",
        "background_date": date(2026, 10, 19),
        "background_memo": "",
        "targets": targets,
        "adds_requirement": False,
        "baseline_number": 1,
    }
    values.update(overrides)
    return ChangeRequestFacts(**values)  # type: ignore[arg-type]


def test_instruction_lists_targets_by_kind() -> None:
    text = build_cr_instruction(
        _facts(
            (
                Target("FR-005", "content", "参照ファイルは5つまで添付できる"),
                Target("FR-007", "content"),
                Target("FR-009", "delete"),
            )
        )
    )

    lines = text.splitlines()
    assert lines[0].startswith("次の合意済みの変更要求(CR-3)を反映して書き直す。")
    assert "unchanged" in lines[0]
    assert lines[1] == "タイトル: 参照ファイルを5つまでにする"
    assert lines[2] == "内容: 添付できる参照ファイルを3つから5つに増やす"
    assert lines[3] == "対象の要件:"
    assert lines[4] == "- FR-005(内容の変更): 変更後の案「参照ファイルは5つまで添付できる」"
    assert lines[5] == "- FR-007(内容の変更): 上の「内容」のとおりに変える"
    assert lines[6] == "- FR-009(削除): この要件を消す"
    assert ADDS_REQUIREMENT_LINE not in text


def test_instruction_for_new_requirement_only() -> None:
    text = build_cr_instruction(_facts((), adds_requirement=True, content="  "))

    assert "対象の要件:" not in text
    assert "内容: —" in text
    assert text.endswith(ADDS_REQUIREMENT_LINE)


def test_join_instructions_skips_empty() -> None:
    assert join_instructions(["指摘の指示", "", "CR の指示"]) == "指摘の指示\n\nCR の指示"
    assert join_instructions(["", ""]) == ""
