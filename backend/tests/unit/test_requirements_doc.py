"""要件定義書の model・組み立て・md・検証(純粋関数)。

SUT: app/documents/requirements の各関数。ドライバ: このテスト。
スタブ不要 ── 対象が純粋(副作用なし)で、LLM・DB を呼ばないため。観点の表は PointState を
直接作って渡す。
"""

from app.documents.requirements import (
    RequirementDraft,
    RequirementRow,
    RequirementsDraft,
    RequirementsModel,
    UserSceneDraft,
    assign_ids,
    build_model,
    cap_source,
    known_points,
    mark_adopted,
    parse_requirement_id,
    render_requirements,
    requirement_id,
    source_cell,
    source_counts,
    validate_requirements,
)
from app.hearing import PointState, initial_states


def _states(**statuses: str) -> dict[str, PointState]:
    states = initial_states()
    for key, status in statuses.items():
        states[key] = PointState(key, status, f"{key}の決定")  # type: ignore[arg-type]
    return states


def _row(req_id: str, kind: str = "FR", **kwargs) -> RequirementRow:
    values = {"text": "要件", "source": "user", "points": ["core"], "priority": "Must"}
    values.update(kwargs)
    if kind != "FR":
        values["priority"] = None
    return RequirementRow(id=req_id, kind=kind, **values)  # type: ignore[arg-type]


# ---- ID ----


def test_requirement_id_round_trip() -> None:
    assert requirement_id("NFR", 7) == "NFR-007"
    assert parse_requirement_id("NFR-007") == ("NFR", 7)
    assert parse_requirement_id("REQ-001") is None
    assert parse_requirement_id("FR001") is None


def test_assign_ids_numbers_new_rows_per_kind() -> None:
    ids, next_seq = assign_ids([("FR", ""), ("NFR", ""), ("FR", "")], None)

    assert ids == ["FR-001", "NFR-001", "FR-002"]
    assert next_seq == {"FR": 3, "NFR": 2, "CON": 1}


def test_assign_ids_keeps_previous_id_and_never_reuses_removed_ids() -> None:
    previous = RequirementsModel(
        purpose="p",
        rows=[_row("FR-001"), _row("FR-002")],
        next_seq={"FR": 4, "NFR": 1, "CON": 1},  # FR-003 は前の前の版で消えた
    )

    ids, next_seq = assign_ids([("FR", "fr-002"), ("FR", "")], previous)

    assert ids == ["FR-002", "FR-004"]
    assert next_seq["FR"] == 5


def test_assign_ids_rejects_unknown_wrong_kind_and_duplicate_prev_ids() -> None:
    previous = RequirementsModel(purpose="p", rows=[_row("FR-001"), _row("NFR-001", "NFR")])

    ids, _ = assign_ids(
        [("FR", "FR-001"), ("FR", "FR-001"), ("CON", "NFR-001"), ("FR", "FR-099")], previous
    )

    assert ids == ["FR-001", "FR-002", "CON-001", "FR-003"]


# ---- 出どころの上限 ----


def test_cap_source_follows_point_status() -> None:
    states = _states(
        core="decided", availability="adopted", security="open", migration="not_applicable"
    )

    assert cap_source("user", ["core"], states) == "user"
    assert cap_source("user", ["availability"], states) == "adopted"  # 採用の観点は「採用」まで
    assert cap_source("user", ["security"], states) == "assumed"  # 未決の観点は「推測」
    assert cap_source("adopted", ["migration"], states) == "assumed"  # 対象外も「推測」
    assert cap_source("assumed", ["core"], states) == "assumed"  # 下げる方向はそのまま


def test_cap_source_without_grounding_points_is_assumed() -> None:
    states = _states(scale="decided")

    assert cap_source("user", [], states) == "assumed"
    assert cap_source("user", ["scale"], states) == "assumed"  # 規模だけでは上げない
    assert cap_source("unknown", ["core"], _states(core="decided")) == "assumed"


def test_cap_source_uses_strongest_point() -> None:
    states = _states(core="decided", security="open")

    assert cap_source("user", ["security", "core"], states) == "user"


def test_known_points_drops_unknown_and_duplicates() -> None:
    assert known_points(["core", "nope", " core", "security"]) == ["core", "security"]


# ---- model の組み立て ----


def test_build_model_assigns_ids_caps_sources_and_normalizes() -> None:
    states = _states(core="decided", users="decided", availability="adopted")
    draft = RequirementsDraft(
        purpose=" 予約を楽にする ",
        users=(UserSceneDraft("店員", "開店前に確認", "user"), UserSceneDraft(" ", "x", "user")),
        in_scope=("予約", " "),
        out_of_scope=("決済",),
        requirements=(
            RequirementDraft(
                kind="FR", text="予約できる", source="user", points=("core",), priority="Must"
            ),
            RequirementDraft(
                kind="FR", text="履歴", source="user", points=("future",), priority="??"
            ),
            RequirementDraft(
                kind="NFR",
                text="半日止まってよい",
                source="user",
                points=("availability",),
                category="止まる",
            ),
            RequirementDraft(
                kind="CON", text="Python", source="user", points=("tech",), category="技術スタック"
            ),
            RequirementDraft(kind="XX", text="捨てる", source="user"),
            RequirementDraft(kind="FR", text="  ", source="user"),
        ),
    )

    model = build_model(draft, None, states, "personal")

    assert model.purpose == "予約を楽にする"
    assert [u.user for u in model.users] == ["店員"]
    assert model.users[0].source == "user"
    assert model.in_scope == ["予約"]
    assert [(r.id, r.source) for r in model.rows] == [
        ("FR-001", "user"),
        ("FR-002", "assumed"),  # 将来の拡張は未決
        ("NFR-001", "adopted"),  # 可用性は採用
        ("CON-001", "assumed"),  # 技術スタックは未決
    ]
    assert model.rows[1].priority == "Must"  # 知らない優先度は Must
    assert model.rows[2].category == "可用性"  # NFR は IPA の大項目にそろえる
    assert model.rows[3].category == "技術スタック"
    assert model.next_seq == {"FR": 3, "NFR": 2, "CON": 2}
    # 生成した時点の観点の状態を写して持つ(付録・検証が読む)
    assert model.point_statuses["availability"] == "adopted"
    assert model.point_statuses["security"] == "open"
    assert model.scale == "personal"


def test_mark_adopted_changes_only_assumed_row() -> None:
    model = RequirementsModel(
        purpose="p",
        rows=[_row("FR-001"), _row("NFR-001", "NFR", source="assumed", category="可用性")],
    )

    adopted = mark_adopted(model, "NFR-001")

    assert adopted is not None
    assert [r.source for r in adopted.rows] == ["user", "adopted"]
    assert mark_adopted(model, "FR-001") is None  # 推測でない行は変えない
    assert mark_adopted(model, "FR-099") is None


# ---- md ----


def _statuses(**statuses: str) -> dict[str, str]:
    return {key: state.status for key, state in _states(**statuses).items()}


def _sample_model() -> RequirementsModel:
    return RequirementsModel(
        purpose="予約を楽にする",
        point_statuses=_statuses(scale="decided", core="decided", migration="not_applicable"),
        scale="personal",
        out_of_scope=["決済"],
        rows=[
            _row("FR-001", text="予約できる"),
            _row("FR-002", text="履歴を見る", priority="Should", points=["future"]),
            _row(
                "NFR-001",
                "NFR",
                text="HTTPS",
                source="assumed",
                category="セキュリティ",
                points=["security"],
            ),
            _row("CON-001", "CON", text="Python", category="技術スタック", points=["tech"]),
        ],
    )


def test_render_requirements_keeps_headings_and_marks_assumed() -> None:
    md = render_requirements(_sample_model())

    for heading in (
        "# 1. 要件定義書",
        "## 1.1 ",
        "## 1.2 ",
        "## 1.3 ",
        "## 1.4 ",
        "## 1.5 ",
        "## 1.6 ",
    ):
        assert heading in md
    assert "| FR-002 | Should | 履歴を見る | ユーザー | 将来の拡張 |" in md
    assert "| NFR-001 | セキュリティ | HTTPS | **推測** | セキュリティ |" in md
    assert "推測の要件(NFR-001)" in md
    assert "**推測** = ドキュメント生成時に AI が推測" in md  # 凡例(画面の注釈と同じ言い方)
    assert "- Out of Scope: 決済" in md
    assert "| 規模 | ユーザーが決定(個人・小規模) | — |" in md
    assert "| 移行性 | 対象外 | — |" in md
    assert "| コア機能 | ユーザーが決定 | FR-001 |" in md
    assert "| 目的・課題 | 未決 | 1.1 |" in md


def test_render_requirements_without_rows_writes_none() -> None:
    md = render_requirements(RequirementsModel(purpose="p"))

    assert md.count("(なし)") == 4  # 1.2・1.4・1.5・1.6


def test_source_cell_emphasizes_assumed_only() -> None:
    assert source_cell("assumed") == "**推測**"
    assert source_cell("adopted") == "採用"


# ---- 検証 ----


def test_source_counts() -> None:
    assert source_counts(_sample_model()) == {"user": 3, "adopted": 0, "assumed": 1}


def test_validate_requirements_reports_each_code() -> None:
    model = RequirementsModel(
        purpose="p",
        rows=[
            _row("FR-001", priority="Should"),
            _row("NFR-001", "NFR", source="assumed", category="移行性", points=["migration"]),
        ],
        point_statuses=_statuses(migration="not_applicable"),
        scale="personal",
    )

    issues = validate_requirements(model)

    by_code = {i.code: i for i in issues}
    assert by_code["NO_MUST_REQUIREMENT"].level == "major"
    assert by_code["REQUIRED_POINT_OPEN"].targets[0] == "scale"
    assert by_code["REQUIREMENT_ON_NOT_APPLICABLE_POINT"].targets == ("NFR-001",)
    assert by_code["ASSUMED_REQUIREMENTS"].targets == ("NFR-001",)
    assert by_code["ASSUMED_REQUIREMENTS"].level == "minor"


def test_validate_requirements_clean_model_has_no_issues() -> None:
    keys = (
        "scale",
        "purpose",
        "users",
        "core",
        "scope",
        "exceptions",
        "tech",
        "availability",
        "performance",
        "security",
    )
    model = RequirementsModel(
        purpose="p",
        rows=[_row("FR-001")],
        point_statuses=_statuses(**dict.fromkeys(keys, "decided")),
        scale="personal",
    )

    assert validate_requirements(model) == []
