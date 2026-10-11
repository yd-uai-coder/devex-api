"""変わったもの(承認時の写しと今の model の、要素の単位の差分)のテスト。

SUT: diff_elements / to_trace_changes / ElementChange(app/detailed_design/impact/diff.py)、
     Element.body(app/detailed_design/impact/elements.py)、パッケージの re-export
     (app/detailed_design/impact)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、DB や LLM を呼ばないため。前と後の model は fixture の
dict を書き換えて作る(写しの読み書きはサービス層の責務)。
"""

import copy

from tests.fixtures.change_impact import REPOSITORY, ROUTE, SERVICE, impact_documents, impact_stages

from app.detailed_design import er_table_definitions
from app.detailed_design.impact import (
    Change,
    DesignModels,
    Element,
    ElementChange,
    build_elements,
    diff_elements,
    to_trace_changes,
)

NEW_SERVICE = "app/services/reservation_service.py"


def _elements(stages: dict[int, dict], documents: dict[str, dict] | None = None) -> list[Element]:
    return build_elements(DesignModels.from_dicts(stages, documents or impact_documents()))


def _summary(changes: list[ElementChange]) -> list[tuple[str, str]]:
    return [(c.key, c.change) for c in changes]


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_function_edit_is_a_content_change_with_before_and_after():
    before = impact_stages()
    after = copy.deepcopy(before)
    after[1]["functions"][1]["name"] = "予約を検索する"

    changes = diff_elements(_elements(before), _elements(after))

    assert _summary(changes) == [("fn:F-02", "content")]
    [change] = changes
    assert change.stage == 1
    assert change.label == "F-02 予約を検索する"
    assert change.before is not None and "予約の一覧を見る" in change.before
    assert change.after is not None and "予約を検索する" in change.after
    assert to_trace_changes(changes) == [Change("fn:F-02", "content")]


# --- 判定 ---


def test_same_models_have_no_changes():
    assert diff_elements(_elements(impact_stages()), _elements(impact_stages())) == []


def test_module_rename_is_detected_and_reference_only_edits_are_not_changes():
    """モジュールの改名は rename(前のキー + 後のキー)。依存する側のモジュールの依存先の欄を新しい
    名前に書き換えただけなら、変わったものに入れない(改名の直接の参照元として印が付く)。"""
    before = impact_stages()
    after = copy.deepcopy(before)
    after[4]["modules"][1]["path"] = NEW_SERVICE
    after[4]["modules"][0]["depends_on"] = [NEW_SERVICE]

    changes = diff_elements(_elements(before), _elements(after), stages=[4])

    assert _summary(changes) == [(f"module:{SERVICE}", "rename")]
    rename = changes[0]
    assert rename.new_key == f"module:{NEW_SERVICE}"
    assert (rename.before, rename.after) == (SERVICE, NEW_SERVICE)
    assert to_trace_changes(changes) == [Change(f"module:{SERVICE}", "rename")]


def test_reference_edit_with_other_edits_is_still_a_change():
    before = impact_stages()
    after = copy.deepcopy(before)
    after[4]["modules"][1]["path"] = NEW_SERVICE
    after[4]["modules"][0]["depends_on"] = [NEW_SERVICE, REPOSITORY]

    changes = diff_elements(_elements(before), _elements(after), stages=[4])

    assert (f"module:{ROUTE}", "content") in _summary(changes)


def test_removed_and_added_are_not_renames_when_contents_differ():
    before = impact_stages()
    after = copy.deepcopy(before)
    after[6]["logics"] = [
        {"module": SERVICE, "function": "register", "pseudo": [{"text": "処理する"}]},
        {"module": SERVICE, "function": "search", "pseudo": [{"text": "検索する"}]},
    ]

    changes = diff_elements(_elements(before), _elements(after), stages=[6])

    assert _summary(changes) == [
        (f"logic:{SERVICE}::list", "removed"),
        (f"logic:{SERVICE}::search", "added"),
    ]
    # 増えた要素は影響を求めない。消えた要素は前のキーで求める
    assert to_trace_changes(changes) == [Change(f"logic:{SERVICE}::list", "content")]


def test_ambiguous_pairs_are_not_renames():
    """内容が同じ組が1対1でなければ、改名とみなさない(どれがどれか決まらない)。"""
    before = impact_stages()
    after = copy.deepcopy(before)
    after[3]["cells"] = [
        {"function_id": "F-01", "table": "bookings", "ops": "C"},
        {"function_id": "F-02", "table": "bookings", "ops": "R"},
    ]

    changes = diff_elements(_elements(before), _elements(after), stages=[3])

    # テーブル1つの消えた・増えたは1対1なので改名。CRUD は改名とみなさない種類
    assert ("table:reservations", "rename") in _summary(changes)
    assert ("crud:F-01×reservations", "removed") in _summary(changes)
    assert ("crud:F-01×bookings", "added") in _summary(changes)


def test_draft_flag_and_derived_refs_are_not_changes():
    """AI の下書きの印と、別の段階から導く辺(手順の行 → モジュールのテーブル)は内容に入れない。"""
    before = impact_stages()
    after = copy.deepcopy(before)
    after[3]["cells"][0]["draft"] = True
    after[5]["procedures"][0]["steps"][2]["db"] = "—"

    changes = diff_elements(_elements(before), _elements(after), stages=[3, 4])

    assert changes == []


def test_stage_filter_limits_to_that_stage():
    before = impact_stages()
    after = copy.deepcopy(before)
    after[1]["functions"][0]["summary"] = "変えた"
    after[7]["crosscutting"][1]["policy"] = "リポジトリで commit"

    assert _summary(diff_elements(_elements(before), _elements(after), stages=[7])) == [
        ("cc:トランザクション", "content")
    ]
    assert len(diff_elements(_elements(before), _elements(after))) == 2


def test_document_elements_are_compared_by_their_rows():
    documents = impact_documents()
    changed = copy.deepcopy(documents)
    changed["requirements"]["rows"][2]["text"] = "本人の予約だけを見られる(管理者は全件)"

    changes = diff_elements(
        _elements(impact_stages(), documents), _elements(impact_stages(), changed), stages=[0]
    )

    assert _summary(changes) == [("req:NFR-001", "content")]


def test_empty_before_marks_everything_added():
    after = _elements({4: impact_stages()[4]})

    changes = diff_elements([], after, stages=[4])

    assert {c.change for c in changes} == {"added"}
    assert {c.key for c in changes} == {f"module:{p}" for p in (ROUTE, SERVICE, REPOSITORY)}


def test_table_definitions_from_er_are_compared():
    """ER のテーブル定義を渡すと、列の追加などテーブルの内容の変更も差分に入る。"""
    er = {
        "notation": "er",
        "elements": [{"id": "t1", "name": "reservations", "kind": "table", "columns": []}],
    }
    changed = copy.deepcopy(er)
    changed["elements"][0]["columns"].append({"name": "note", "type": "TEXT"})

    def elements(model: dict) -> list[Element]:
        tables = er_table_definitions(model)
        return build_elements(DesignModels.from_dicts(impact_stages(), {}, tables))

    changes = diff_elements(elements(er), elements(changed), stages=[3])

    assert _summary(changes) == [("table:reservations", "content")]
    assert changes[0].after is not None and '"note"' in changes[0].after
