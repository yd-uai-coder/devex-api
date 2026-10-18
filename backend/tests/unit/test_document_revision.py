"""文書の再生成を前の版の差分にとどめる仕組み(変更なしの行を前の版から写す)のテスト。

SUT: rows_text / changed_rows / changed_rows_text / requirement_rows / external_design_rows /
     internal_design_rows / revision_rule(app/documents/revision.py)、
     4文書の build_model の変更なし(`unchanged`)と実装計画書の`prev_key`、要件定義書の写した行の
     出どころ(`cap_source`のかけ直し)、4文書の`REVISION`(節の分け方)、
     4文書の下書きのメッセージ(前の版の行・上流の変わった行)、
     upstream_changes(app/services/doc_generator_service.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、DB や LLM を呼ばないため。下書きは偽の LLM の見本の
構造化出力(tests/fixtures)を`to_draft`で変換して作る。
"""

from tests.fixtures.external_design import external_design_output
from tests.fixtures.requirements import requirements_output
from tests.fixtures.simple_documents import implementation_plan_output, internal_design_output

from app.documents.external_design import build_model as build_external_design_model
from app.documents.external_design.drafting import REVISION as EXTERNAL_REVISION
from app.documents.external_design.drafting import SYSTEM_PROMPT as EXTERNAL_PROMPT
from app.documents.external_design.drafting import build_external_design_messages
from app.documents.external_design.drafting import to_draft as to_external_design_draft
from app.documents.implementation_plan import build_model as build_plan_model
from app.documents.implementation_plan.drafting import REVISION as PLAN_REVISION
from app.documents.implementation_plan.drafting import SYSTEM_PROMPT as PLAN_PROMPT
from app.documents.implementation_plan.drafting import build_implementation_plan_messages
from app.documents.implementation_plan.drafting import to_draft as to_plan_draft
from app.documents.internal_design import build_model as build_internal_design_model
from app.documents.internal_design.drafting import REVISION as INTERNAL_REVISION
from app.documents.internal_design.drafting import SYSTEM_PROMPT as INTERNAL_PROMPT
from app.documents.internal_design.drafting import build_internal_design_messages
from app.documents.internal_design.drafting import to_draft as to_internal_design_draft
from app.documents.requirements import build_model as build_requirements_model
from app.documents.requirements.drafting import REVISION as REQUIREMENTS_REVISION
from app.documents.requirements.drafting import SYSTEM_PROMPT as REQUIREMENTS_PROMPT
from app.documents.requirements.drafting import build_requirements_messages
from app.documents.requirements.drafting import to_draft as to_requirements_draft
from app.documents.revision import (
    NONE_TEXT,
    RevisionInputs,
    changed_rows,
    changed_rows_text,
    external_design_rows,
    internal_design_rows,
    requirement_rows,
    revision_rule,
    rows_text,
)
from app.hearing import PointState, initial_states
from app.services.doc_generator_service import upstream_changes


def _requirements(text: str = "備品を予約できる", *, unchanged: bool = False, previous=None):
    output = requirements_output(prev_id="FR-001" if previous else "", text=text)
    output.requirements[0].unchanged = unchanged
    output.requirements[1].prev_id = "NFR-001" if previous else ""
    return build_requirements_model(to_requirements_draft(output), previous, {}, None)


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_unchanged_requirement_keeps_previous_row_verbatim():
    first = _requirements()
    reworded = _requirements("備品を予約できるようにする", unchanged=True, previous=first)

    assert reworded.rows[0] == first.rows[0]
    assert changed_rows(requirement_rows(first), requirement_rows(reworded)) == []
    # 変更なしでない行は、今までどおり書いた中身で作る(ID は引き継ぐ)
    rewritten = _requirements("備品を予約できるようにする", previous=first)
    assert rewritten.rows[0].id == "FR-001"
    assert rewritten.rows[0].text == "備品を予約できるようにする"
    assert changed_rows(requirement_rows(first), requirement_rows(rewritten)) == [
        "要件 FR-001(変わった)"
    ]


# --- revision.py ---


def test_changed_rows_lists_new_changed_and_removed_rows():
    first = _requirements()
    second = _requirements("備品を予約・取り消しできる", previous=first)
    second.rows.append(second.rows[1].model_copy(update={"id": "NFR-002"}))
    second.rows.pop(1)

    assert changed_rows(requirement_rows(first), requirement_rows(second)) == [
        "要件 FR-001(変わった)",
        "要件 NFR-002(新しい)",
        "要件 NFR-001(消えた)",
    ]
    # 前の版が無い(初回の生成)なら空
    assert changed_rows({}, requirement_rows(second)) == []
    assert changed_rows_text([]) == NONE_TEXT
    assert changed_rows_text(["要件 FR-001(変わった)"]) == "- 要件 FR-001(変わった)"


def test_rows_text_is_one_json_per_row():
    model = _requirements()

    assert rows_text(model.rows).splitlines()[0].startswith('{"id": "FR-001"')
    assert rows_text([]) == NONE_TEXT


def test_revision_rule_names_previous_triggers_and_context():
    rule = revision_rule(
        RevisionInputs(
            previous=("前の版の画面",),
            triggers={"上流の変わった行": "変わった行", "修正指示": "指摘"},
            context=("要件定義書",),
        )
    )
    assert rule.startswith("- 【前の版の画面】(前の版)があるときは")
    assert "変更のきっかけは【上流の変わった行】(変わった行)・【修正指示】(指摘)。" in rule
    assert "【要件定義書】は読むだけで、それ自体はきっかけにしない。" in rule
    assert rule.endswith("前の版が「なし」なら unchanged は false")
    no_context = revision_rule(RevisionInputs(previous=("前の版",), triggers={"会話": "依頼"}))
    assert "読むだけ" not in no_context


def _sections(messages) -> set[str]:  # noqa: ANN001
    """下書きのメッセージの本文の節(`## 名前`)。入力の md は見出しの無い文にして渡す。"""
    return {line[3:] for line in str(messages[-1].content).splitlines() if line.startswith("## ")}


def test_every_prompt_section_is_classified_as_previous_trigger_or_context():
    """4文書の下書きのプロンプトの節は、すべて前の版・きっかけ・参照のどれかに分けてある
    (新しい入力の節を足して、きっかけの一覧を直し忘れると落ちる)。規則の文はその分け方から
    組み立て、文書に無い節(要件定義書の【上流の変わった行】・外部設計書の【会話】)を名指ししない。"""
    requirements = build_requirements_messages("会話", {}, None, None)
    external = build_external_design_messages("要件", None, ())
    internal = build_internal_design_messages("要件", "外部", None, ())
    plan = build_implementation_plan_messages("要件", "内部", None, ())
    cases = [
        (REQUIREMENTS_REVISION, REQUIREMENTS_PROMPT, requirements),
        (EXTERNAL_REVISION, EXTERNAL_PROMPT, external),
        (INTERNAL_REVISION, INTERNAL_PROMPT, internal),
        (PLAN_REVISION, PLAN_PROMPT, plan),
    ]
    for revision, prompt, messages in cases:
        assert _sections(messages) == revision.sections
        assert revision_rule(revision) in prompt
        assert "修正指示" in revision.triggers
    assert "【上流の変わった行】" not in revision_rule(REQUIREMENTS_REVISION)
    assert "【会話】" not in revision_rule(EXTERNAL_REVISION)


def test_requirements_prompt_rewrites_rows_whose_point_was_decided_again():
    """聞き直しで観点の決めたこと・状態・規模が変わったら、その観点の要件は書き直す
    (観点の表の変化も、変更のきっかけに数える)。"""
    when = REQUIREMENTS_REVISION.triggers["観点の表"]
    assert when.startswith("決めたこと・状態・規模が前の版の要件と食い違う")
    assert "その観点を points に持つ要件を書き直し" in when
    assert f"【観点の表】({when})" in REQUIREMENTS_PROMPT


def test_unchanged_requirement_source_is_capped_again_by_current_points():
    """写した行も、出どころは今の観点の表で上限をかけ直す(観点が決定から採用に変わったら、
    写した行も「採用」に下がる。上がりはしない)。"""
    decided = initial_states()
    decided["core"] = PointState("core", "decided", "予約", ("m1",))
    output = requirements_output(text="備品を予約できる")
    output.requirements[0].points = ["core"]
    output.requirements[0].source = "user"
    first = build_requirements_model(to_requirements_draft(output), None, decided, None)
    assert first.rows[0].source == "user"

    adopted = {**decided, "core": PointState("core", "adopted", "予約", ("m2",))}
    again = requirements_output(prev_id="FR-001", text="言い換え")
    again.requirements[0].unchanged = True
    second = build_requirements_model(to_requirements_draft(again), first, adopted, None)
    assert second.rows[0].source == "adopted"
    assert second.rows[0].text == first.rows[0].text

    third = build_requirements_model(to_requirements_draft(again), second, decided, None)
    assert third.rows[0].source == "adopted"


# --- 外部設計書 ---


def test_external_design_keeps_unchanged_screen_and_api():
    first = build_external_design_model(
        to_external_design_draft(external_design_output()), None, None
    )
    output = external_design_output()
    output.screens[0].role = "言い換えた役割"
    output.screens[0].unchanged = True
    output.apis[0].summary = "言い換えた概要"
    output.apis[0].unchanged = True
    second = build_external_design_model(to_external_design_draft(output), first, None)

    assert second.screens == first.screens
    assert second.apis == first.apis
    assert external_design_rows(second).keys() == {"画面 SCR-001", "API POST /api/v1/reservations"}
    output.apis[0].unchanged = False
    third = build_external_design_model(to_external_design_draft(output), first, None)
    assert third.apis[0].summary == "言い換えた概要"


def test_external_design_messages_show_previous_rows_and_changed_requirements():
    first = build_external_design_model(
        to_external_design_draft(external_design_output()), None, None
    )

    [_, human] = build_external_design_messages("要件", first, ["要件 FR-001(変わった)"])

    assert '"path": "/api/v1/reservations"' in human.content
    assert "## 上流の変わった行\n- 要件 FR-001(変わった)" in human.content
    [_, initial] = build_external_design_messages("要件", None)
    assert f"## 前の版の API\n{NONE_TEXT}" in initial.content


# --- 内部設計書 ---


def test_internal_design_keeps_unchanged_rows_and_dataflow_id():
    first = build_internal_design_model(to_internal_design_draft(internal_design_output()), None)
    output = internal_design_output()
    output.modules[0].responsibility = "言い換え"
    output.modules[0].unchanged = True
    output.apis[0].summary = "言い換え"
    output.apis[0].unchanged = True
    output.dataflows[0].flows[0].data = "言い換え"
    output.dataflows[0].unchanged = True
    second = build_internal_design_model(to_internal_design_draft(output), first)

    assert second.modules[0] == first.modules[0]
    assert second.apis == first.apis
    assert second.dataflows == first.dataflows
    # 変更なしでない行は書いた中身で作る
    assert second.modules[1].responsibility == "登録"
    assert list(internal_design_rows(second)) == [
        "モジュール app/api/reservations.py",
        "モジュール app/services/reservation.py",
        "処理別データフロー DF-1(POST /api/v1/reservations)",
    ]


def test_internal_design_messages_show_previous_rows_and_changed_upstream():
    first = build_internal_design_model(to_internal_design_draft(internal_design_output()), None)

    [_, human] = build_internal_design_messages("要件", "外部", first, ["画面 SCR-001(変わった)"])

    assert '"id": "DF-1"' in human.content
    assert '"path": "app/api/reservations.py"' in human.content
    assert "- 画面 SCR-001(変わった)" in human.content


# --- 実装計画書 ---


def test_plan_task_prev_key_carries_key_and_unchanged_keeps_content():
    first = build_plan_model(to_plan_draft(implementation_plan_output()), None)
    [task] = first.milestones[0].tasks
    output = implementation_plan_output(title="予約の登録を作る")
    output.milestones[0].tasks[0].prev_key = task.key
    output.milestones[0].tasks[0].modules = ["app/services/reservation.py"]
    renamed = build_plan_model(to_plan_draft(output), first)

    # 名前が言い換えられても、AI が指した前の版のキーを引き継ぐ。中身は書いたもの
    assert renamed.milestones[0].tasks[0].key == task.key
    assert renamed.milestones[0].tasks[0].modules == ["app/services/reservation.py"]
    output.milestones[0].tasks[0].unchanged = True
    kept = build_plan_model(to_plan_draft(output), first)
    assert kept.milestones[0].tasks[0] == task


def test_plan_messages_show_previous_tasks_with_keys():
    first = build_plan_model(to_plan_draft(implementation_plan_output()), None)

    [system, human] = build_implementation_plan_messages("要件", "内部", first, [])

    assert '"key": "U-001"' in human.content
    assert f"## 上流の変わった行\n{NONE_TEXT}" in human.content
    assert "prev_key" in system.content


# --- 生成のサービス ---


def test_upstream_changes_compares_previous_and_new_upstream_models():
    first = _requirements()
    second = _requirements("備品を予約・取り消しできる", previous=first)
    external = build_external_design_model(
        to_external_design_draft(external_design_output()), None, None
    )
    previous = {"requirements": first.model_dump(mode="json"), "external_design": None}

    assert upstream_changes("external_design", previous, second, None, None) == [
        "要件 FR-001(変わった)"
    ]
    # 外部設計書の前の版が無ければ、外部設計書からの変わった行は無い(初回の生成と同じ)
    assert upstream_changes("internal_design", previous, second, external, None) == [
        "要件 FR-001(変わった)"
    ]
    assert upstream_changes("requirements", previous, second, None, None) == []
