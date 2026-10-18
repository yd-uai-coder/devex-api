"""AI の下書きからテスト仕様書の model を組み立てる(純粋関数)。

- テストの ID は結合・総合ごとに並び順で振る(`IT-001`・`ST-001`)。
- 結合テストの対象は、外部設計書の同じ API(`endpoint_key`で照合)の書き方にそろえる。外部設計書に
  無い API は書かれたまま残し、検証で指摘する。処理・単位は対象の API から引く(`links`)。
- 要件ID は`FR-001`の形にそろえ、形の違うものは書かれたまま残す(検証で指摘する)。
- 生成した時点の要件・API・画面と入力の版を写す(検証と「古い」の判定が読む)。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.detailed_design.api_list import endpoint_key, parse_trigger
from app.detailed_design.requirement_trace import normalize_req_id
from app.documents.external_design.assemble import split_ids
from app.documents.external_design.model import ExternalDesignModel
from app.documents.requirements.model import RequirementsModel
from app.documents.test_spec.links import ApiLink
from app.documents.test_spec.model import (
    CaseLevel,
    SpecCase,
    SpecPolicy,
    TestSpecModel,
    Viewpoint,
    case_id,
)


@dataclass(frozen=True)
class CaseDraft:
    level: CaseLevel
    target: str
    viewpoint: Viewpoint
    precondition: str = ""
    steps: str = ""
    expected: str = ""
    design_gap: str = ""
    req_ids: tuple[str, ...] = ()
    screens: tuple[str, ...] = ()


@dataclass(frozen=True)
class TestSpecDraft:
    __test__ = False  # pytest に集めさせない(名前が Test で始まるため)

    policy: SpecPolicy
    cases: tuple[CaseDraft, ...] = ()


def api_key(target: str) -> str | None:
    """結合テストの対象(`POST /api/v1/x`)の照合のキー。API の形でなければ None。"""
    parsed = parse_trigger(target)
    return endpoint_key(*parsed) if parsed is not None else None


def _req_ids(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(normalize_req_id(v) or v for v in split_ids(values)))


def _case(
    draft: CaseDraft,
    seq: int,
    api_labels: Mapping[str, str],
    links: Mapping[str, ApiLink],
) -> SpecCase:
    target = " ".join(draft.target.split())
    link = ApiLink()
    if draft.level == "integration":
        key = api_key(target)
        if key is not None:
            target = api_labels.get(key, target)
            link = links.get(key, ApiLink())
    return SpecCase(
        id=case_id(draft.level, seq),
        level=draft.level,
        target=target,
        viewpoint=draft.viewpoint,
        precondition=draft.precondition.strip(),
        steps=draft.steps.strip(),
        expected=draft.expected.strip(),
        design_gap=draft.design_gap.strip(),
        req_ids=_req_ids(draft.req_ids),
        screens=split_ids(draft.screens),
        functions=list(link.functions),
        units=list(link.units),
    )


def build_model(
    draft: TestSpecDraft,
    *,
    requirements: RequirementsModel | None,
    external: ExternalDesignModel | None,
    links: Mapping[str, ApiLink],
    inputs: Mapping[str, int | None],
) -> TestSpecModel:
    apis = external.apis if external else []
    api_labels = {endpoint_key(a.method, a.path): a.label for a in apis}
    cases: dict[CaseLevel, list[SpecCase]] = {"integration": [], "system": []}
    for draft_case in draft.cases:
        level_cases = cases[draft_case.level]
        level_cases.append(_case(draft_case, len(level_cases) + 1, api_labels, links))
    rows = requirements.rows if requirements else []
    return TestSpecModel(
        policy=draft.policy,
        integration=cases["integration"],
        system=cases["system"],
        known_req_ids=[r.id for r in rows],
        req_priorities={r.id: r.priority for r in rows if r.priority is not None},
        must_fr_ids=[r.id for r in rows if r.kind == "FR" and r.priority == "Must"],
        known_apis=list(api_labels.values()),
        known_screens=[s.id for s in external.screens] if external else [],
        inputs=dict(inputs),
    )
