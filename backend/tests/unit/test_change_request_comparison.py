"""変更要求(CR)の影響の予測と、反映で付いた印の比較のテスト。

SUT: 純粋関数 compare・applied_marks_from_json と Comparison.count
     (app/change_requests/comparison.py)、ChangeRequestService.comparison(規模の予測と実際の
     数も)、スキーマ ChangeRequestComparisonRead・ScaleComparisonRead、
     ChangeRequestNotAppliedError、
     ルート get_change_request_comparison(app/api/routes/change_requests.py)
ドライバ: 各テスト関数
スタブ: 純粋関数のテストはスタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため。
サービスとルートは、インメモリ SQLite(`db_session`)と、段階1〜8 承認済みの fixture
(`create_impact_project`)・合意済みの版を使い、文書の反映は FakeLLM(構造化出力の見本)で通す
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.requirements import requirements_output

from app.api.routes.change_requests import get_change_request_comparison
from app.change_requests import (
    AppliedMark,
    PredictedItem,
    PredictedTarget,
    Prediction,
    applied_marks_from_json,
    compare,
)
from app.models.project import Project
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import (
    ChangeRequestComparisonRead,
    ChangeRequestCreate,
    ChangeRequestDecision,
)
from app.services.baseline_service import BaselineService
from app.services.change_request_service import ChangeRequestService
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import ChangeRequestNotAppliedError

_AT = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


def _item(key: str, stage: int) -> PredictedItem:
    return PredictedItem(
        key=key,
        kind="function",
        stage=stage,
        label=key,
        path=("req:FR-001", key),
        reason="予測の理由",
        requirement_ids=("FR-001",),
    )


def _prediction(*items: PredictedItem) -> Prediction:
    return Prediction(
        targets=(PredictedTarget("FR-001", "content", "予約を登録する"),),
        items=items,
        unpredicted_new_requirement=False,
    )


def _mark(key: str, stage: int, *, reviewed: bool = False) -> AppliedMark:
    return AppliedMark(key=key, label=key, stage=stage, reason="実際の理由", reviewed=reviewed)


def test_compare_classifies_each_element() -> None:
    prediction = _prediction(
        _item("api:POST", 0),  # 文書の側
        _item("fn:F-01", 1),  # 一致
        _item("proc:F-01", 5),  # 承認した段階なのに届かなかった
        _item("unit:U-002", 8),  # 承認していない段階
    )
    applied = [_mark("fn:F-01", 1, reviewed=True), _mark("cc:認証", 7)]

    comparison = compare(prediction, applied, marked_stages={1, 5, 7})

    rows = [(i.key, i.status, i.predicted_only_reason) for i in comparison.items]
    assert rows == [
        ("api:POST", "predicted_only", "document_side"),
        ("fn:F-01", "matched", None),
        ("proc:F-01", "predicted_only", "not_changed"),
        ("cc:認証", "actual_only", None),
        ("unit:U-002", "predicted_only", "unmarked_stage"),
    ]
    matched = comparison.items[1]
    assert (matched.predicted_reason, matched.actual_reason, matched.reviewed) == (
        "予測の理由",
        "実際の理由",
        True,
    )
    assert comparison.items[3].requirement_ids == ()
    assert comparison.items[0].reviewed is None
    assert (
        comparison.count("matched"),
        comparison.count("predicted_only"),
        comparison.count("actual_only"),
    ) == (1, 3, 1)


def test_applied_marks_from_json_skips_broken_rows() -> None:
    marks = applied_marks_from_json(
        [
            {"key": "fn:F-01", "label": "登録", "stage": 1, "reason": "r", "reviewed": True},
            {"label": "キーが無い", "stage": 1},
            {"key": "x", "stage": "数でない"},
        ]
    )

    assert marks == [AppliedMark("fn:F-01", "登録", 1, "r", True)]
    assert applied_marks_from_json(None) == []


async def _agreed_project(session: AsyncSession) -> Project:
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    service = ChangeRequestService(session)
    await service.create(
        project,
        ChangeRequestCreate.model_validate(
            {
                "title": "登録で確認メールを送る",
                "content": "予約の登録の後に確認メールを送る",
                "background_source": "meeting",
                "targets": [{"requirement_id": "FR-001", "kind": "content"}],
            }
        ),
        created_by=None,
    )
    await service.explain(project, 1, actor_id=None)
    await service.decide(
        project,
        1,
        ChangeRequestDecision.model_validate(
            {"decision": "agreed", "decided_at": _AT, "reason": "合意"}
        ),
        actor_id=None,
    )
    return project


async def test_comparison_after_reflection(db_session: AsyncSession) -> None:
    project = await _agreed_project(db_session)
    with pytest.raises(ChangeRequestNotAppliedError):  # 反映の前は比べられない
        await get_change_request_comparison(1, db_session, project)
    llm = FakeLLM(
        content_sequence=["## 自己診断\n最重要: 特になし"],
        structured_by_schema=upstream_outputs(
            requirements_output(prev_id="FR-001", text="予約を登録し、確認メールを送る")
        ),
    )
    status = await DocGeneratorService(db_session).request_generation(
        project, start_from="requirements", cr_number=1
    )
    await DocGeneratorService(db_session).generate(
        project.id,
        project.user_id,
        status_before_generation=status,
        start_from="requirements",
        cr_number=1,
        llm=llm,
    )

    read = await get_change_request_comparison(1, db_session, project)

    assert isinstance(read, ChangeRequestComparisonRead)
    assert (read.label, read.status, read.saved) == ("CR-1", "applied", True)
    by_key = {i.key: i for i in read.items}
    assert by_key["fn:F-01"].status == "matched"
    assert by_key["fn:F-01"].status_label == "一致"
    # 画面・API は印が付かないので、予測だけ(文書の側)
    documents = [i for i in read.items if i.stage == 0]
    assert documents and all(i.predicted_only_reason == "document_side" for i in documents)
    assert documents[0].predicted_only_reason_label == "文書の側(印が付かない)"
    assert sum(read.counts.values()) == len(read.items)
    # 規模: 作業単位・ファイル・テストは反映の印から今の設計で、画面・API は確認の一覧で数える
    scale = {row.kind: row for row in read.scale}
    assert list(scale) == ["unit", "file", "test", "screen", "api"]
    assert {k: scale[k].basis for k in scale} == {
        "unit": "marks",
        "file": "marks",
        "test": "marks",
        "screen": "document_checks",
        "api": "document_checks",
    }
    assert (scale["screen"].predicted, scale["screen"].actual) == (1, 0)
    assert scale["test"].predicted is None  # 束にテスト仕様書が無い
    assert (scale["unit"].predicted, scale["file"].predicted) == (1, 3)
    assert scale["unit"].actual is not None and scale["unit"].actual >= 1
    assert scale["file"].actual == 3  # 3層のファイルを共有するので、単位が増えても3件
