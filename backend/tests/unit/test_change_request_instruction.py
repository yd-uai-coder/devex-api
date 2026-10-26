"""合意した CR からの再生成の指示のテスト。

SUT: app.change_requests(build_cr_instruction / join_instructions。合意した案の1行は
     options_from_rows で組んだ案。競合の今の文の1行は Conflict で組んだ競合)
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

from datetime import date

from app.change_requests import (
    ChangeRequestFacts,
    Conflict,
    Target,
    build_cr_instruction,
    join_instructions,
    options_from_rows,
)
from app.change_requests.instruction import (
    ADDS_REQUIREMENT_LINE,
    CONFLICT_LINE,
    CONFLICT_REMOVED_LINE,
    OPTION_LINE,
    ORIGIN_LINE,
)


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


def test_instruction_lists_new_requirement_origins() -> None:
    text = build_cr_instruction(
        _facts(
            (),
            adds_requirement=True,
            origin_labels=("SCR-002 設定画面", "FR-004 参照ファイルを添付できる"),
        )
    )

    lines = text.splitlines()
    assert lines[-2] == ADDS_REQUIREMENT_LINE
    assert lines[-1] == ORIGIN_LINE.format(
        origins="SCR-002 設定画面・FR-004 参照ファイルを添付できる"
    )
    # 新しい要件を足さない CR は、起点を書かない
    assert ORIGIN_LINE.split("{")[0] not in build_cr_instruction(_facts((Target("FR-005"),)))


def test_join_instructions_skips_empty() -> None:
    assert join_instructions(["指摘の指示", "", "CR の指示"]) == "指摘の指示\n\nCR の指示"
    assert join_instructions(["", ""]) == ""


def test_instruction_names_the_agreed_option() -> None:
    """合意で選んだ案があれば、タイトル・内容の後ろに案の名前と結果の1行を置く(対象は CR の対象。
    合意で選んだ案の対象になっている)。選んでいなければ置かない。"""
    options = options_from_rows(
        [
            ("5つまで", [Target("FR-005")], "画面を直す"),
            ("10まで", [Target("FR-005")], "保存の\n容量が増える"),
        ]
    )
    text = build_cr_instruction(
        _facts((Target("FR-005"),), options=options, decision_option=2)
    )

    lines = text.splitlines()
    assert lines[3] == "合意した案: 案2 10まで(結果: 保存の 容量が増える)"
    assert lines[4] == "対象の要件:"
    assert "合意した案" not in build_cr_instruction(_facts((Target("FR-005"),), options=options))
    assert OPTION_LINE.startswith("合意した案")


def test_conflicting_target_gets_current_text_line() -> None:
    """対象の要件がほかの CR の反映で変わっていれば、その行の下に今の文(消えていればその旨)を
    添える。競合の無い対象には添えない。"""
    conflicts = [
        Conflict("req:FR-005", "FR-005", (1, 2), "参照ファイルは3つまで", "参照は4つまで", 9),
        Conflict("req:FR-009", "FR-009", (2,), "古い文", None, 10),
    ]
    text = build_cr_instruction(
        _facts(
            (
                Target("FR-005", "content", "参照ファイルは5つまで添付できる"),
                Target("FR-007", "content"),
                Target("FR-009", "delete"),
            )
        ),
        conflicts,
    )

    lines = text.splitlines()
    assert lines[5] == CONFLICT_LINE.format(numbers="CR-1・CR-2", current="参照は4つまで")
    assert lines[6] == "- FR-007(内容の変更): 上の「内容」のとおりに変える"
    assert lines[8] == CONFLICT_REMOVED_LINE.format(numbers="CR-2")
