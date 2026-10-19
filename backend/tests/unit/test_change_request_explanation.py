"""変更要求(CR)の説明書(5節の md)と要約の照合のテスト。

SUT: app.change_requests の build_explanation / with_summary / check_summary / affected_units /
     explanation_filename と、
     Explanation(markdown・fingerprint)・ChangeRequestFacts・AffectedUnits・SummaryCheck
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため。入力は、要件 FR-001・FR-002・
NFR-001 と段階1〜8 を持つ fixture の model を束の写しの形に並べ、要件定義書に観点の状態と推測の
要件、テスト仕様書の写しを足したもの
"""

from datetime import date

from tests.fixtures.change_impact import impact_documents, impact_stages

from app.change_requests import (
    ChangeRequestFacts,
    Prediction,
    Target,
    affected_units,
    build_explanation,
    check_summary,
    explanation_filename,
    predict_from_snapshots,
    with_summary,
)


def _snapshots(*, test_spec: bool = True) -> dict[str, dict]:
    documents = impact_documents()
    rows = documents["requirements"]["rows"]
    rows[0]["points"] = ["core", "scope"]
    rows[1]["source"] = "assumed"
    documents["requirements"]["point_statuses"] = {"core": "decided", "scope": "open"}
    stages = {f"stage:{n}": model for n, model in impact_stages().items()}
    snapshots: dict[str, dict] = {**documents, **stages}
    if test_spec:
        snapshots["test_spec"] = {
            "integration": [
                {"id": "IT-001", "functions": ["F-01"], "req_ids": []},
                {"id": "IT-002", "functions": ["F-02"], "req_ids": []},
            ],
            "system": [{"id": "ST-001", "functions": [], "req_ids": ["FR-001"]}],
        }
    return snapshots


def _facts(targets: tuple[Target, ...], **overrides: object) -> ChangeRequestFacts:
    values: dict[str, object] = {
        "number": 1,
        "title": "予約の登録に\n備考を足す",
        "status": "draft",
        "content": "予約の登録で備考を3行まで書けるようにする",
        "background_source": "meeting",
        "background_date": date(2026, 10, 18),
        "background_memo": "定例で依頼|急ぎ",
        "targets": targets,
        "adds_requirement": False,
        "baseline_number": 1,
    }
    values.update(overrides)
    return ChangeRequestFacts(**values)  # type: ignore[arg-type]


def _build(
    targets: tuple[Target, ...],
    *,
    snapshots: dict[str, dict] | None = None,
    excluded: list[tuple[str, str]] | None = None,
    **overrides: object,
):
    snapshots = snapshots if snapshots is not None else _snapshots()
    facts = _facts(targets, **overrides)
    prediction = predict_from_snapshots(
        snapshots, list(targets), adds_requirement=facts.adds_requirement
    )
    return build_explanation(facts, prediction, snapshots, excluded or [])


def test_sections_are_built_in_order() -> None:
    """題・基準の行と1〜5節が順に並び、1節は内容と背景(表のセルは改行・縦棒を逃がす)。"""
    explanation = _build((Target("FR-001", "content", "予約に備考を付けて登録できる"),))
    md = explanation.markdown

    assert md.startswith("# 変更の説明書 CR-1: 予約の登録に 備考を足す\n")
    assert "- 基準の合意済みの版: B-1" in md and "- CR の状態: 下書き" in md
    headings = [line for line in md.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 1. 変更の内容と背景",
        "## 2. 対象の要件",
        "## 3. 影響の一覧",
        "## 4. 関わる未決事項",
        "## 5. 影響を受ける作業単位とテストの数",
    ]
    assert "| 出どころ | 打ち合わせ |" in md
    assert "| 日付 | 2026-10-18 |" in md
    assert "| メモ | 定例で依頼\\|急ぎ |" in md


def test_targets_show_agreed_text_and_proposal() -> None:
    """2節は合意済みの文と変更後の案。削除は「(削除)」、案が空なら1節を参照。"""
    md = _build(
        (
            Target("FR-001", "content", "予約に備考を付けて登録できる"),
            Target("FR-002", "delete"),
            Target("NFR-001"),
        ),
        adds_requirement=True,
    ).markdown

    assert "| FR-001 | 内容の変更 | 予約を登録できる | 予約に備考を付けて登録できる |" in md
    assert "| FR-002 | 削除 | 予約の一覧を見られる | (削除) |" in md
    assert "| NFR-001 | 内容の変更 | 本人の予約だけを見られる | (1節の内容を参照) |" in md
    assert "このほかに、新しい要件を足します(内容は1節)。" in md


def test_impact_is_grouped_by_place_with_notes() -> None:
    """3節は置き場ごとの表(理由・届いた対象)。束に入っていないものと新しい要件の注記。"""
    explanation = _build(
        (Target("FR-001"),),
        excluded=[("stage:6", "reviewing"), ("test_spec", "absent")],
        adds_requirement=True,
    )
    md = explanation.markdown

    assert "### 文書(要件・画面・API)" in md
    assert "### 段階8 実装手順書" in md
    assert "| 届いた対象 |" in md and "| FR-001 |" in md
    assert "段階6 処理ロジック(レビュー中)・テスト仕様書(無い)" in md
    assert "新しい要件を足す分は、つながりの起点が無いため予測に入っていません。" in md


def test_no_impact_and_no_open_points() -> None:
    """要素も未決事項も無ければ、その旨を1行で書く(新しい要件だけの CR)。"""
    md = _build((), adds_requirement=True).markdown

    assert "既存の要件は変えません。" in md
    assert "見直しが要る要素は予測されませんでした。" in md
    assert "対象の要件に関わる未決事項はありません。" in md
    assert "- 作業単位: 0件" in md


def test_open_points_and_assumed_requirements() -> None:
    """4節は対象の要件の根拠の観点のうち、束の写しで未決のもの(決まった観点は出さない)と、
    推測の要件。"""
    md = _build((Target("FR-001"), Target("FR-002"))).markdown

    assert "| MVP の範囲 | FR-001 |" in md
    assert "コア機能" not in md
    assert "| FR-002 | 予約の一覧を見られる |" in md


def test_units_and_tests_are_counted() -> None:
    """5節は予測の作業単位(段階7・8 を単位で1つに)と、処理か対象の要件に当たるテストの数。"""
    explanation = _build((Target("FR-001"),))
    md = explanation.markdown

    assert explanation.units.units == ("M-01-T02",)
    assert "- 作業単位: 1件(M-01-T02)" in md
    assert explanation.units.integration_tests == ("IT-001",)
    assert explanation.units.system_tests == ("ST-001",)
    assert "- テスト: 2件(結合テスト 1件・総合テスト 1件)" in md

    without_spec = _build((Target("FR-001"),), snapshots=_snapshots(test_spec=False))
    assert "テスト仕様書が無いため、数えていません" in without_spec.markdown


def test_affected_units_without_prediction() -> None:
    empty = Prediction(targets=(), items=(), unpredicted_new_requirement=False)

    units = affected_units([Target("FR-009")], empty, {"integration": [], "system": []})

    assert (units.units, units.integration_tests, units.has_test_spec) == ((), (), True)


def test_fingerprint_follows_the_markdown() -> None:
    """同じ入力は同じ md と fingerprint。CR の文が変われば fingerprint も変わる。"""
    first = _build((Target("FR-001"),))
    again = _build((Target("FR-001"),))
    edited = _build((Target("FR-001"),), content="備考を5行まで書けるようにする")

    assert first.markdown == again.markdown
    assert first.fingerprint == again.fingerprint
    assert len(first.fingerprint) == 64
    assert edited.fingerprint != first.fingerprint


def test_explanation_filename() -> None:
    assert explanation_filename(12) == "cr-12-explanation.md"


def test_with_summary_puts_summary_before_sections() -> None:
    explanation = _build((Target("FR-001"),))

    output = with_summary(explanation, " 予約の登録に備考を足します。 ")

    assert output.startswith(explanation.header + "\n\n## 要約\n\n予約の登録に備考を足します。\n\n")
    assert output.endswith(explanation.body + "\n")
    assert with_summary(explanation, None) == explanation.markdown + "\n"
    assert with_summary(explanation, "  ") == explanation.markdown + "\n"


def test_check_summary_finds_unknown_ids_and_numbers() -> None:
    """要約の要件 ID・数字のうち、5節に無いものを返す(全角の数字も、ID の中の数字は除く)。"""
    explanation = _build((Target("FR-001"),))

    ok = check_summary("FR-001 を直し、備考を3行まで書けるようにします。", explanation)
    bad = check_summary(
        "FR-001 と FR-007 を直し、備考を５行に。工数は5日、FR-007 は再掲。", explanation
    )

    assert ok.ok
    assert bad.unknown_requirement_ids == ("FR-007",)
    assert bad.unknown_numbers == ("5",)
    assert not bad.ok
    # 英字に続く数字は名前の一部とみなす
    assert check_summary("[E2E Fake] S3 に置きます。", explanation).ok
