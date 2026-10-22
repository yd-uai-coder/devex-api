"""変更要求(CR)の説明書(5節の md。案のある CR は6節)と要約の照合のテスト。

SUT: app.change_requests の build_explanation / with_summary / check_summary /
     explanation_filename と、
     Explanation(markdown・fingerprint・scale・option_scales)・ChangeRequestFacts(新しい要件の
     起点の`origin_labels`も。選択肢の`options`・`decision_option`も)・SummaryCheck
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため。入力は、要件 FR-001・FR-002・
NFR-001 と段階1〜8 を持つ fixture の model を束の写しの形に並べ、要件定義書に観点の状態と推測の
要件、テスト仕様書の写しを足したもの
"""

from datetime import date

from tests.fixtures.change_impact import impact_documents, impact_stages

from app.change_requests import (
    ChangeOptions,
    ChangeRequestFacts,
    Target,
    build_explanation,
    check_summary,
    explanation_filename,
    options_from_rows,
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
    stage_states: dict[int, str] | None = None,
    origins: tuple[str, ...] = (),
    **overrides: object,
):
    snapshots = snapshots if snapshots is not None else _snapshots()
    facts = _facts(targets, **overrides)
    prediction = predict_from_snapshots(
        snapshots, list(targets), adds_requirement=facts.adds_requirement, origins=origins
    )
    return build_explanation(facts, prediction, snapshots, excluded or [], stage_states)


def test_sections_are_built_in_order() -> None:
    """題・読み手向けの1文・基準の行と1〜5節が順に並び、各節の見出しの次に読み手向けの1文を
    置く。1節は内容と背景(表のセルは改行・縦棒を逃がす)。"""
    explanation = _build((Target("FR-001", "content", "予約に備考を付けて登録できる"),))
    md = explanation.markdown
    lines = md.splitlines()

    assert md.startswith("# 変更の説明書 CR-1: 予約の登録に 備考を足す\n\nこの文書は、")
    assert "- 基準の合意済みの版: B-1(前回合意した要件と設計の一式)" in md
    assert "- CR の状態: 下書き" in md
    headings = [line for line in lines if line.startswith("## ")]
    assert headings == [
        "## 1. 変更の内容と背景",
        "## 2. 対象の要件",
        "## 3. 変更の規模",
        "## 4. 影響の一覧",
        "## 5. 合意の前に確かめたい点",
    ]
    leads = [lines[lines.index(h) + 2] for h in headings]
    assert all(lead.startswith("この節は、") for lead in leads)
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
    """4節は置き場ごとの表(要素は名前を先に・理由・届いた対象)。束に入っていないものと新しい
    要件の注記。"""
    explanation = _build(
        (Target("FR-001"),),
        excluded=[("stage:6", "reviewing"), ("test_spec", "absent")],
        adds_requirement=True,
    )
    md = explanation.markdown

    assert "### 文書(要件・画面・API)" in md
    assert "### 段階8 実装手順書" in md
    assert "| 届いた対象 |" in md and "| FR-001 |" in md
    assert "| 予約を登録する(F-01) | この要件を持つ | FR-001 |" in md
    assert "段階6 処理ロジック(レビュー中)・テスト仕様書(無い)" in md
    assert "新しい要件を足す分は、つながりの起点が無いため予測に入っていません。" in md


def test_new_requirement_origins_are_shown_in_targets_and_impact() -> None:
    """新しい要件の起点は、2節の「新しい要件を足します」の後と、4節の「届いた対象」に出る
    (名前を先に)。起点があれば「予測に入っていません」の注記は出ない。"""
    md = _build(
        (Target("FR-002"),),
        adds_requirement=True,
        origins=("screen:SCR-001",),
        origin_labels=("SCR-001 予約画面",),
    ).markdown

    assert "新しい要件の予測の起点: 予約画面(SCR-001)。" in md
    assert "(対象の要件と新しい要件の起点からのつながりをたどったもの)" in md
    assert "| 予約画面(SCR-001) | 新しい要件の起点 | FR-002・新しい要件(起点 SCR-001) |" in md
    assert "起点が無いため予測に入っていません" not in md


def test_no_impact_and_no_open_points() -> None:
    """要素も確かめたい点も無ければ、その旨を1行で書く(新しい要件だけの CR は、起点が無いので
    規模を数えられない)。"""
    md = _build((), adds_requirement=True).markdown

    assert "既存の要件は変えません。" in md
    assert "見直しが要る要素は予測されませんでした。" in md
    assert "合意の前に確かめたい点はありません。" in md
    assert "| 作業単位 | 数えられない |  |" in md


def test_open_points_and_assumed_requirements_are_questions() -> None:
    """5節は対象の要件の根拠の観点のうち束の写しで未決のもの(決まった観点は出さない。観点の
    問いの例を添える)と、推測の要件(文は変えずに引く)を、問いの形で書く。"""
    md = _build((Target("FR-001"), Target("FR-002"))).markdown

    assert (
        "- 「MVP の範囲」がまだ決まっていません(関わる要件: FR-001)。"
        "最初の版で作らないと決めているものはありますか。"
    ) in md
    assert "コア機能" not in md
    assert (
        "- FR-002 の文は、AI が補った推測で、まだ確かめていません。この文のとおりでよいですか。"
        "「予約の一覧を見られる」"
    ) in md


def test_scale_section_counts_and_explains_uncountable() -> None:
    """3節は規模の表(種類・数・内訳)。数えられない種類は理由を表の下にまとめ、手間の見込みを
    別に確かめるよう1文足す。古い段階の注記も表の下に出る。"""
    explanation = _build((Target("FR-001"),), stage_states={7: "outdated"})
    md = explanation.markdown

    assert explanation.scale.of("unit").names == ("予約を登録する(M-01-T02)",)
    assert "| 作業単位 | 1件 | 予約を登録する(M-01-T02) |" in md
    assert "| テスト | 2件 | 結合テスト 1件(IT-001)・総合テスト 1件(ST-001) |" in md
    assert "| 画面 | 1件 | 予約画面(SCR-001) |" in md
    assert "- 作業単位・ファイル: 古いまま合意済みの版に写した段階から数えたため" in md
    assert "数えられませんでした" not in md

    without_spec = _build((Target("FR-001"),), snapshots=_snapshots(test_spec=False)).markdown
    assert "| テスト | 数えられない |  |" in without_spec
    assert "テストは数えられませんでした(理由は表の下)。" in without_spec
    assert "- テスト: 合意済みの版に入っていない文書がある(テスト仕様書)" in without_spec


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
    """要約の要件 ID・数字のうち、説明書に無いものを返す(全角の数字も、ID の中の数字は除く)。"""
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


def _options() -> ChangeOptions:
    return options_from_rows(
        [
            ("登録だけ", [Target("FR-001", "content", "備考を付けて登録できる")], "小さく\n済む"),
            ("登録と一覧", [Target("FR-001"), Target("FR-002", "delete")], "一覧も直す"),
        ],
        recommended=1,
        reason="早く出せる",
    )


def test_options_add_a_section_and_compare_scale_by_option() -> None:
    """案のある CR は2節の後に「選択肢」を入れて6節。規模は種類 × 案の表で、影響の一覧はすべての
    案を合わせた範囲。2節の空の案は3節を参照させる。"""
    explanation = _build((Target("FR-001"), Target("FR-002")), options=_options())
    md = explanation.markdown
    lines = md.splitlines()

    assert [line for line in lines if line.startswith("## ")] == [
        "## 1. 変更の内容と背景",
        "## 2. 対象の要件",
        "## 3. 選択肢",
        "## 4. 変更の規模",
        "## 5. 影響の一覧",
        "## 6. 合意の前に確かめたい点",
    ]
    assert "| FR-001 | 内容の変更 | 予約を登録できる | (3節の案を参照) |" in md
    assert "おすすめは案1です。理由: 早く出せる。" in md
    assert "### 案1 登録だけ(おすすめ)" in lines
    assert "### 案2 登録と一覧" in lines
    assert "| FR-001 | 内容の変更 | 備考を付けて登録できる |" in md
    assert "| FR-002 | 削除 | (削除) |" in md
    assert "結果: 小さく 済む" in lines
    assert "| 種類 | 案1 登録だけ | 案2 登録と一覧 |" in lines
    units = [s.scale.of("unit").count for s in explanation.option_scales]
    assert f"| 作業単位 | {units[0]}件 | {units[1]}件 |" in lines
    assert "すべての案を合わせた範囲です。" in md


def test_after_agreement_the_chosen_option_is_shown() -> None:
    """合意で案を選んだ後は、選択肢の節に合意した案を示し、規模は今の表に戻る。"""
    explanation = _build(
        (Target("FR-001", "content", "備考を付けて登録できる"),),
        options=_options(),
        decision_option=1,
        status="agreed",
    )
    md = explanation.markdown

    assert "合意した案: 案1 登録だけ。" in md
    assert "### 案1 登録だけ(おすすめ・合意した案)" in md
    assert "| 種類 | 数 | 内訳 |" in md
    assert explanation.option_scales == ()
    assert "すべての案を合わせた範囲" not in md


def test_option_without_targets_says_so() -> None:
    """対象の無い案(新しい要件を足すだけ)は、既存の要件を変えないと書く。"""
    options = options_from_rows([("足すだけ", [], "x"), ("直す", [Target("FR-001")], "y")])
    md = _build((Target("FR-001"),), options=options, adds_requirement=True).markdown

    assert "既存の要件は変えません(新しい要件を足すだけです)。" in md
    assert "おすすめは" not in md


def test_no_options_keeps_the_five_sections() -> None:
    """案の無い CR は、案の空の値を渡しても今の5節の md と同じ(要約を古くしない)。"""
    targets = (Target("FR-001"),)

    assert _build(targets, options=ChangeOptions()).markdown == _build(targets).markdown
