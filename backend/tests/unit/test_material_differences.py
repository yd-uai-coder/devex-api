"""資料と合意済みの内容の違いの取り出し(純粋関数)のテスト。

SUT: app/materials/differences.py の agreed_listing・build_difference_messages・normalize_quote・
     quote_found・resolve_key・check_difference・check_differences・fresh_differences(パッケージ
     app.materials の re-export から)
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で、DB にも LLM にも触らないため。束の写しは dict で、LLM の
     出力は DifferenceListOut で直接渡す
"""

from datetime import date

from tests.fixtures.change_impact import impact_documents

from app.materials import (
    MIN_QUOTE_CHARS,
    CheckedDifference,
    DifferenceItemOut,
    DifferenceListOut,
    MaterialFacts,
    agreed_listing,
    build_difference_messages,
    check_difference,
    check_differences,
    fresh_differences,
    normalize_quote,
    quote_found,
    resolve_key,
)

_BODY = """# 定例 議事録
- 予約の登録の後に、**確認メール** を送ってほしい。
- 一覧は今のままでよい。
> 取消は 前日まで
> とする。
"""


def _snapshots() -> dict[str, dict]:
    docs = impact_documents()
    docs["requirements"]["in_scope"] = ["予約の受付"]
    docs["requirements"]["out_of_scope"] = ["決済"]
    docs["requirements"]["users"] = [{"user": "利用者", "scene": "席を予約する", "source": "user"}]
    docs["external_design"]["integrations"] = [{"service": "SendGrid", "purpose": "メール"}]
    return docs


def _item(**overrides: str) -> DifferenceItemOut:
    values = {
        "kind": "change",
        "target_key": "req:FR-001",
        "origin_key": "",
        "content": "登録の後に確認メールを送る",
        "quote": "予約の登録の後に、確認メールを送ってほしい。",
    }
    values.update(overrides)
    return DifferenceItemOut.model_validate(values)


def test_listing_has_keys_and_reference_scope() -> None:
    listing = agreed_listing(_snapshots())

    assert "- req:FR-001 | 要件 FR | 予約を登録できる" in listing.text
    assert "- screen:SCR-001 | 画面 予約画面 | 要件: FR-001, FR-002" in listing.text
    assert "- api:POST /api/v1/reservations | API | 画面: SCR-001 / 要件: FR-001" in listing.text
    assert "- integration:SendGrid | 外部連携 SendGrid | 目的: メール" in listing.text
    assert "- users | 利用者と使う場面 | 利用者: 席を予約する" in listing.text
    assert "- 範囲の外: 決済" in listing.text
    assert {"req:FR-001", "users", "screen:SCR-001", "integration:SendGrid"} <= listing.keys


def test_listing_without_documents_is_empty() -> None:
    listing = agreed_listing({})
    assert listing.keys == frozenset()


def test_messages_carry_listing_and_material() -> None:
    listing = agreed_listing(_snapshots())
    material = MaterialFacts("議事録", "第3回 定例", date(2026, 10, 20), _BODY)

    system, human = build_difference_messages(listing, material)

    assert "question" in str(system.content)
    assert str(human.content).startswith(listing.text)
    assert "# 資料(議事録「第3回 定例」2026-10-20)" in str(human.content)
    assert str(human.content).endswith(_BODY)


def test_quote_ignores_width_whitespace_and_markdown() -> None:
    assert normalize_quote("**ＡＢＣ** ｄ\n e") == "ABCde"
    # 強調の記号・改行をまたぐ引用・前後の括弧
    assert quote_found("予約の登録の後に、確認メールを送ってほしい。", _BODY)
    assert quote_found("「取消は前日までとする。」", _BODY)
    assert not quote_found("取消は前々日までとする。", _BODY)
    assert not quote_found("「」", _BODY)


def test_resolve_key_adds_prefix_only_when_unique() -> None:
    keys = {"req:FR-001", "screen:SCR-001", "req:X", "screen:X"}
    assert resolve_key("FR-001", keys) == "req:FR-001"
    assert resolve_key(" req:FR-001 ", keys) == "req:FR-001"
    assert resolve_key("X", keys) == "X"  # 2つに当たるので決めない
    assert resolve_key("FR-009", keys) == "FR-009"


def test_change_with_literal_quote_passes() -> None:
    listing = agreed_listing(_snapshots())

    checked = check_difference(_item(target_key="FR-001"), listing, _BODY)

    assert (checked.status, checked.target_key) == ("unconfirmed", "req:FR-001")
    assert checked.reasons == ()


def test_quote_problems_are_rejected_with_reasons() -> None:
    listing = agreed_listing(_snapshots())

    missing = check_difference(_item(quote="確認の電話をかけてほしい"), listing, _BODY)
    ellipsis = check_difference(_item(quote="予約の登録の後に…送ってほしい"), listing, _BODY)
    short = check_difference(_item(quote="一覧は"), listing, _BODY)
    empty = check_difference(_item(quote=" "), listing, _BODY)

    assert missing.reasons == ("引用が資料に無い",)
    assert ellipsis.reasons == ("引用を省略でつないでいる",)
    assert len(normalize_quote("一覧は")) < MIN_QUOTE_CHARS
    assert short.reasons == ("引用が短すぎる",)
    assert empty.reasons == ("引用が無い",)
    assert missing.status == "rejected"


def test_kind_and_target_combinations() -> None:
    listing = agreed_listing(_snapshots())

    def reasons(**overrides: str) -> tuple[str, ...]:
        return check_difference(_item(**overrides), listing, _BODY).reasons

    assert reasons(kind="delete", target_key="") == ("削除に対象が無い",)
    assert reasons(target_key="req:FR-009") == ("対象 req:FR-009 が束に無い",)
    assert reasons(kind="add", target_key="req:FR-001") == (
        "追加に対象がある(新しいものは起点で示す)",
    )
    assert reasons(kind="add", target_key="", origin_key="SCR-001") == ()
    # 起点は合意の要素のどれでもよい(利用者を広げる追加は、利用者と使う場面から)
    assert reasons(kind="add", target_key="", origin_key="users") == ()
    assert reasons(kind="add", target_key="", origin_key="screen:SCR-009") == (
        "起点 screen:SCR-009 が束に無い",
    )
    assert reasons(kind="question", target_key="") == ()
    assert reasons(content="  ") == ("内容が空",)
    # 起点は追加のときだけ持つ(ほかの種類では捨てる)
    question = check_difference(
        _item(kind="question", target_key="", origin_key="req:FR-001"), listing, _BODY
    )
    assert question.origin_key == ""
    added = check_difference(_item(kind="add", target_key="", origin_key="SCR-001"), listing, _BODY)
    assert added.origin_key == "screen:SCR-001"


def test_check_differences_keeps_order() -> None:
    listing = agreed_listing(_snapshots())
    out = DifferenceListOut(differences=[_item(), _item(kind="question", target_key="")])

    checked = check_differences(out, listing, _BODY)

    assert [c.kind for c in checked] == ["change", "question"]


def test_rerun_skips_kept_and_repeated_differences() -> None:
    def diff(kind: str, quote: str) -> CheckedDifference:
        return CheckedDifference(kind, "req:FR-001", "", "内容", quote)  # type: ignore[arg-type]

    kept = [diff("change", "予約の登録の後に、**確認メール** を送ってほしい。")]
    new = [
        diff("change", "予約の登録の後に、確認メールを送ってほしい。"),  # 捨てた違いと同じ
        diff("delete", "一覧は今のままでよい。"),
        diff("delete", "一覧は 今のままでよい。"),  # この回の前の方と同じ
        diff("change", "一覧は今のままでよい。"),  # 種類が違う
    ]

    assert [(d.kind, d.quote) for d in fresh_differences(new, kept)] == [
        ("delete", "一覧は今のままでよい。"),
        ("change", "一覧は今のままでよい。"),
    ]
