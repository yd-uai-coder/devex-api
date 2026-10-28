"""変更要求(CR)の変更の規模(作業単位・ファイル・テスト・画面・API の数。純粋関数)。

docs/external_design.md 2.16節「説明とすり合わせ」。影響の予測(prediction.py)の要素と、基準の
合意済みの版(束)の写しから、種類ごとの数を決定的に数える。LLM は使わず、工数は出さない。

- 作業単位: 予測の段階7のタスク・段階8の手順書を、単位のキーで1つにまとめる。
- ファイル: その単位の手順書の「作成・変更するファイル」と、段階7のタスクのモジュール・設定の
  ファイル(`procedure_coverage.unit_files`と同じ材料)。同じパスは1件。手順書の無い単位は段階7
  だけから数え、注記する。
- テスト: 束のテスト仕様書のうち、予測の処理か対象の要件に当たるもの。
- 画面・API: 予測の文書の側の要素。

たどれないときは0件でなく「数えられない」とし、理由を返す(要る段階・文書が束に無い・機能要件の
対象が処理に1つも届かない・新しい要件の起点が無い)。束の段階が承認のままでない(古いなど)だけ
なら数え、少なく出ることがあると注記する。束の後にほかの CR を文書へ反映したとき(束の鮮度。
freshness.py)も数え、今の設計と違うことがあると注記する(`with_baseline_changes`)。注記のうち
この2つが付いた数は「古い」(`ScaleCount.state` = stale)で、「数えられない」と分けて返す。

反映の後は、反映で付いた印の要素を、同じ関数で今の設計から数える(`compare_scale`で予測と並べる)。
画面・API は印が付かないので、文書の側の確認の一覧で確かめた数を並べる。
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, get_args

from app.change_requests.prediction import STAGE_PREFIX, Prediction
from app.change_requests.targets import Target
from app.detailed_design.impact.elements import parse_model
from app.detailed_design.plan import PlanModel
from app.detailed_design.procedure_coverage import unit_files
from app.detailed_design.procedure_doc import PlanUnit, ProcedureDocModel, plan_units

ScaleKind = Literal["unit", "file", "test", "screen", "api"]
SCALE_KINDS: tuple[ScaleKind, ...] = get_args(ScaleKind)
SCALE_KIND_LABEL: dict[ScaleKind, str] = {
    "unit": "作業単位",
    "file": "ファイル",
    "test": "テスト",
    "screen": "画面",
    "api": "API",
}

# 数えられない理由
UncountableReason = Literal[
    "stage_missing", "document_missing", "requirement_unlinked", "new_requirement_unpredicted"
]
UNCOUNTABLE_REASON_LABEL: dict[UncountableReason, str] = {
    "stage_missing": "合意済みの版に入っていない段階がある",
    "document_missing": "合意済みの版に入っていない文書がある",
    "requirement_unlinked": "設計の処理につながらない要件がある",
    "new_requirement_unpredicted": "新しい要件のつながりの起点が無い",
}
# 数えたうえでの注記
ScaleNoteKind = Literal["stage_outdated", "baseline_changed", "planned_modules_only"]
SCALE_NOTE_LABEL: dict[ScaleNoteKind, str] = {
    "stage_outdated": (
        "古いまま合意済みの版に写した段階から数えたため、実際より少なく出ることがある"
    ),
    "baseline_changed": (
        "合意済みの版の後に別の変更を反映したため、今の設計と違うことがある"
    ),
    "planned_modules_only": "手順書の無い作業単位は、実装計画のモジュールから数えた",
}
# 数の状態(数えた / 古い版から数えた / 数えられない)。古いのは、下の注記が付いたもの
ScaleState = Literal["counted", "stale", "uncountable"]
STALE_NOTE_KINDS: frozenset[str] = frozenset({"stage_outdated", "baseline_changed"})

# 作業単位・ファイル・テストを数えるのに要る段階(段階1 = 要件から処理へ、段階7 = 作業単位)
_UNIT_STAGES: tuple[int, ...] = (1, 7)
_TEST_STAGES: tuple[int, ...] = (1,)
# 作業単位・ファイルの数が拠る段階(古いときに注記する)
_UNIT_NOTE_STAGES: tuple[int, ...] = (1, 7)
_FILE_NOTE_STAGES: tuple[int, ...] = (1, 7, 8)
_PLAN_STAGE = 7
_PROCEDURE_DOC_STAGE = 8
# 束の写しの段階の状態のうち、承認のまま(古くない)
APPROVED_STATE = "approved"
# 機能要件の ID の接頭辞(処理につながるはずの要件)
FUNCTIONAL_PREFIX = "FR-"
DOCUMENT_KINDS: tuple[ScaleKind, ...] = ("screen", "api")

# `ID 名前`の形のラベルの先頭の ID(`SCR-001`・`M-01-T02`・`F-01`)
_LEADING_ID = re.compile(r"^([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+) (.+)$")


@dataclass(frozen=True)
class ScaleRemark:
    """数えられない理由、または注記1つ。`detail`は対象(段階・文書・要件 ID・単位)の並び。"""

    code: str
    label: str
    detail: str = ""


@dataclass(frozen=True)
class ScaleCount:
    """種類1つの数。`names`は数えたもの(表示名)、`uncountable`が空でなければ数えられない。
    `notes`は数えたうえでの注記(数えられないときは出さない)。"""

    kind: ScaleKind
    names: tuple[str, ...]
    uncountable: tuple[ScaleRemark, ...] = ()
    notes: tuple[ScaleRemark, ...] = ()

    def __post_init__(self) -> None:
        if self.uncountable and self.notes:
            object.__setattr__(self, "notes", ())

    @property
    def label(self) -> str:
        return SCALE_KIND_LABEL[self.kind]

    @property
    def count(self) -> int | None:
        """数(数えられなければ None)。"""
        return None if self.uncountable else len(self.names)

    @property
    def state(self) -> ScaleState:
        """数えられない・古い版から数えた(少ない・違うことがある)・数えた。"""
        if self.uncountable:
            return "uncountable"
        if any(note.code in STALE_NOTE_KINDS for note in self.notes):
            return "stale"
        return "counted"


@dataclass(frozen=True)
class Scale:
    """CR 1つの規模(種類の順)。テストは結合・総合の内訳も持つ(`has_test_spec`が偽なら、
    テスト仕様書が無く、テストは数えていない)。"""

    counts: tuple[ScaleCount, ...]
    integration_tests: tuple[str, ...] = ()
    system_tests: tuple[str, ...] = ()
    has_test_spec: bool = False

    def of(self, kind: ScaleKind) -> ScaleCount:
        return next(c for c in self.counts if c.kind == kind)

    @property
    def uncountable_kinds(self) -> tuple[ScaleKind, ...]:
        return tuple(c.kind for c in self.counts if c.uncountable)


@dataclass(frozen=True)
class ScaleSources:
    """数える材料。`plan`・`procedure_docs`は段階7・8 の model(無ければ None)、`test_spec`は
    テスト仕様書の model(無ければ None)。`missing_stages`は数えるのに要る段階(1・7)のうち
    無いもの、`outdated_stages`は承認のままでない段階。"""

    plan: PlanModel | None
    procedure_docs: ProcedureDocModel | None
    test_spec: Mapping[str, Any] | None
    has_external_design: bool
    missing_stages: frozenset[int] = frozenset()
    outdated_stages: frozenset[int] = frozenset()


@dataclass(frozen=True)
class ScaleComparisonRow:
    """反映の後の、種類1つの予測の数と実際の数。`basis`は実際の数の出どころ(`marks` = 反映で
    付いた印から今の設計で数えた、`document_checks` = 文書の側の確認の一覧で確かめた数)。"""

    kind: ScaleKind
    predicted: int | None
    actual: int | None
    basis: Literal["marks", "document_checks"]

    @property
    def label(self) -> str:
        return SCALE_KIND_LABEL[self.kind]


def display_name(label: str) -> str:
    """要素のラベルを名前を先にした形にする(`SCR-001 トップページ` → `トップページ(SCR-001)`)。
    先頭が ID でないもの(パス・`GET /…`)と、ID の後ろが「の」「×」で続くものはそのまま。"""
    match = _LEADING_ID.match(label.strip())
    if match is None or match.group(2).startswith(("の", "×")):
        return label.strip()
    return f"{match.group(2).strip()}({match.group(1)})"


def sources_from_snapshots(
    snapshots: Mapping[str, Mapping[str, Any] | None], stage_states: Mapping[int, str]
) -> ScaleSources:
    """束の写し(key → model。入っているものだけ)と、入っている段階の束を作った時点の状態から
    材料を作る。"""
    present = {
        int(key[len(STAGE_PREFIX) :])
        for key, model in snapshots.items()
        if key.startswith(STAGE_PREFIX) and key[len(STAGE_PREFIX) :].isdigit() and model
    }
    return ScaleSources(
        plan=parse_model(PlanModel, snapshots.get(f"{STAGE_PREFIX}{_PLAN_STAGE}")),
        procedure_docs=parse_model(
            ProcedureDocModel, snapshots.get(f"{STAGE_PREFIX}{_PROCEDURE_DOC_STAGE}")
        ),
        test_spec=snapshots.get("test_spec"),
        has_external_design=bool(snapshots.get("external_design")),
        missing_stages=frozenset(s for s in _UNIT_STAGES if s not in present),
        outdated_stages=frozenset(s for s in outdated_stages(stage_states) if s in present),
    )


def outdated_stages(stage_states: Mapping[int, str]) -> frozenset[int]:
    """承認のままでない段階(段階の番号 → 束を作った時点の状態から)。束の鮮度と共通の規則。"""
    return frozenset(s for s, state in stage_states.items() if state != APPROVED_STATE)


def unlinked_requirements(targets: Sequence[Target], prediction: Prediction) -> tuple[str, ...]:
    """機能要件の対象のうち、予測で処理(段階1)に1つも届かなかったもの(対象の順)。"""
    reached = {
        requirement_id
        for item in prediction.items
        if item.kind == "function"
        for requirement_id in item.requirement_ids
    }
    return tuple(
        t.requirement_id
        for t in targets
        if t.requirement_id.startswith(FUNCTIONAL_PREFIX) and t.requirement_id not in reached
    )


def predicted_scale(
    targets: Sequence[Target],
    prediction: Prediction,
    snapshots: Mapping[str, Mapping[str, Any] | None],
    stage_states: Mapping[int, str],
) -> Scale:
    """予測から規模を数える(説明書の3節)。段階1が束に無ければ、要件のつながりは見ない(段階の
    理由だけを出す)。"""
    sources = sources_from_snapshots(snapshots, stage_states)
    unlinked = () if 1 in sources.missing_stages else unlinked_requirements(targets, prediction)
    return count_scale(
        [(item.key, item.label) for item in prediction.items],
        sources,
        requirement_ids=[t.requirement_id for t in targets],
        unlinked=unlinked,
        unpredicted_new_requirement=prediction.unpredicted_new_requirement,
    )


def count_scale(
    items: Sequence[tuple[str, str]],
    sources: ScaleSources,
    *,
    requirement_ids: Collection[str] = (),
    unlinked: Sequence[str] = (),
    unpredicted_new_requirement: bool = False,
) -> Scale:
    """要素(キーとラベル)から、種類ごとの数を求める。テストは要素の処理か`requirement_ids`に
    当たるもの。`unlinked`は処理に届かなかった機能要件(作業単位・ファイル・テストを数えられない)。"""
    labels = dict(items)
    unit_keys = list(
        dict.fromkeys(
            key.partition(":")[2] for key, _ in items if key.startswith(("task:", "unit:"))
        )
    )
    function_ids = {key.partition(":")[2] for key, _ in items if key.startswith("fn:")}
    common = (
        (_remark("new_requirement_unpredicted"),) if unpredicted_new_requirement else ()
    )
    linked = (
        (_remark("requirement_unlinked", "・".join(unlinked)),) if unlinked else ()
    )

    units = _plan_units(sources.plan, unit_keys)
    unit_names = tuple(
        f"{unit.task.title.strip()}({unit.unit_id})"
        if unit is not None
        else display_name(labels.get(f"task:{key}") or labels.get(f"unit:{key}", key))
        for key, unit in units
    )
    files, planned_only = _files(units, sources.procedure_docs)
    tests = _tests(sources.test_spec, function_ids, set(requirement_ids))

    unit_count = ScaleCount(
        "unit",
        unit_names,
        uncountable=(*common, *_missing_stages(sources, _UNIT_STAGES), *linked),
        notes=_outdated(sources, _UNIT_NOTE_STAGES),
    )
    file_notes = _outdated(sources, _FILE_NOTE_STAGES)
    if planned_only:
        file_notes += (_note("planned_modules_only", "・".join(planned_only)),)
    file_count = ScaleCount(
        "file",
        files,
        uncountable=(*common, *_missing_stages(sources, _UNIT_STAGES), *linked),
        notes=file_notes,
    )
    test_missing = () if sources.test_spec is not None else (
        _remark("document_missing", "テスト仕様書"),
    )
    test_count = ScaleCount(
        "test",
        (*tests[0], *tests[1]),
        uncountable=(*common, *_missing_stages(sources, _TEST_STAGES), *test_missing, *linked),
        notes=_outdated(sources, _TEST_STAGES),
    )
    design_missing = () if sources.has_external_design else (
        _remark("document_missing", "外部設計書"),
    )
    document_counts = tuple(
        ScaleCount(
            kind,
            tuple(
                display_name(label) for key, label in items if key.startswith(f"{kind}:")
            ),
            uncountable=(*common, *design_missing),
        )
        for kind in DOCUMENT_KINDS
    )
    return Scale(
        counts=(unit_count, file_count, test_count, *document_counts),
        integration_tests=tests[0],
        system_tests=tests[1],
        has_test_spec=sources.test_spec is not None,
    )


def compare_scale(
    predicted: Scale,
    actual: Scale,
    predicted_keys: Collection[str],
    document_checks: Collection[str],
) -> tuple[ScaleComparisonRow, ...]:
    """予測の数と実際の数を種類ごとに並べる。作業単位・ファイル・テストは反映の印から数えた
    `actual`、画面・API は予測の要素のうち文書の側の確認の一覧で確かめた数。"""
    checked = set(document_checks)
    rows: list[ScaleComparisonRow] = []
    for kind in SCALE_KINDS:
        if kind in DOCUMENT_KINDS:
            count = sum(1 for k in predicted_keys if k.startswith(f"{kind}:") and k in checked)
            rows.append(
                ScaleComparisonRow(kind, predicted.of(kind).count, count, "document_checks")
            )
        else:
            rows.append(
                ScaleComparisonRow(kind, predicted.of(kind).count, actual.of(kind).count, "marks")
            )
    return tuple(rows)


def with_baseline_changes(
    scale: Scale, baseline_number: int, change_request_numbers: Sequence[int]
) -> Scale:
    """束の後に文書へ反映した CR(番号の順)があれば、数えた種類に「今の設計と違うことがある」の
    注記を足す(数えられない種類には足さない)。無ければそのまま。"""
    if not change_request_numbers:
        return scale
    detail = f"B-{baseline_number} の後に " + "・".join(
        f"CR-{n}" for n in change_request_numbers
    )
    note = _note("baseline_changed", detail)
    return replace(
        scale,
        counts=tuple(
            c if c.uncountable else replace(c, notes=(*c.notes, note)) for c in scale.counts
        ),
    )


def _plan_units(
    plan: PlanModel | None, unit_keys: Sequence[str]
) -> list[tuple[str, PlanUnit | None]]:
    """単位のキーを、段階7の並び順にして単位を引く(段階7に無いキーはキーの順で後ろに)。"""
    by_key = {unit.key: unit for unit in plan_units(plan)} if plan is not None else {}
    order = {key: i for i, key in enumerate(by_key)}
    keys = sorted(unit_keys, key=lambda k: (k not in order, order.get(k, 0), k))
    return [(key, by_key.get(key)) for key in keys]


def _files(
    units: Sequence[tuple[str, PlanUnit | None]], docs: ProcedureDocModel | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """単位のファイル(パスで重なりを除く)と、手順書の無い単位の ID。"""
    by_key = {doc.unit_key or doc.unit_id: doc for doc in docs.units} if docs is not None else {}
    paths: dict[str, None] = {}
    planned_only: list[str] = []
    for key, unit in units:
        if unit is None:
            continue
        doc = by_key.get(key)
        if doc is None:
            planned_only.append(unit.unit_id)
        for ref in unit_files(unit, doc, lambda _: None):
            paths.setdefault(ref.path, None)
    return tuple(paths), tuple(planned_only)


def _tests(
    spec: Mapping[str, Any] | None, function_ids: set[str], requirement_ids: set[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    def hits(cases: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
        return tuple(
            str(case.get("id", ""))
            for case in cases
            if function_ids.intersection(case.get("functions", []))
            or requirement_ids.intersection(case.get("req_ids", []))
        )

    spec = spec or {}
    return hits(spec.get("integration", [])), hits(spec.get("system", []))


def _missing_stages(sources: ScaleSources, stages: Sequence[int]) -> tuple[ScaleRemark, ...]:
    missing = [s for s in stages if s in sources.missing_stages]
    if not missing:
        return ()
    return (_remark("stage_missing", "・".join(f"段階{s}" for s in missing)),)


def _outdated(sources: ScaleSources, stages: Sequence[int]) -> tuple[ScaleRemark, ...]:
    outdated = [s for s in stages if s in sources.outdated_stages]
    if not outdated:
        return ()
    return (_note("stage_outdated", "・".join(f"段階{s}" for s in outdated)),)


def _remark(code: UncountableReason, detail: str = "") -> ScaleRemark:
    return ScaleRemark(code, UNCOUNTABLE_REASON_LABEL[code], detail)


def _note(code: ScaleNoteKind, detail: str = "") -> ScaleRemark:
    return ScaleRemark(code, SCALE_NOTE_LABEL[code], detail)
