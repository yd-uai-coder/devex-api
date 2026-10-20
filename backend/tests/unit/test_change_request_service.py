"""変更要求(CR)の作成・更新・状態の遷移・写し・説明書と要約のテスト。

SUT: ChangeRequestService(create / list_for_project / get / update / explain / decide /
     withdraw / copy_rejected / impact / explanation / generate_summary / edit_summary /
     confirm_summary。簡易モードは作成・読み出しとも require_detailed で断る)と
     ExplanationView(summary_state・output)、純粋関数 baseline_requirement_ids /
     checked_targets / predict_for / facts_of、
     スキーマ ChangeRequestCreate・ChangeRequestUpdate・ChangeRequestDecision・
     ChangeRequestWithdraw・ChangeRequestRead・ChangeRequestSummaryRead、共有の AgreementNotes
ドライバ: pytest のテスト関数
スタブ: 永続化と遷移の判定が検証対象のため、テスト用の SQLite のセッションと、
要件 FR-001・FR-002・NFR-001 と承認済みの段階を持つ fixture(`create_impact_project`)から作った
合意済みの版を使う。保存した予測を読み直さないことだけは、予測の関数を例外を投げるスタブに
差し替えて確かめる(monkeypatch)。要約の LLM は、固定の文を返すフェイク(FakeLLM)をサービスに
注入する
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.fake_llm import FakeLLM

from app.change_requests import Target, targets_from_json
from app.models.project import Project
from app.schemas.agreement import AgreementNotes
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestRead,
    ChangeRequestSummaryRead,
    ChangeRequestUpdate,
    ChangeRequestWithdraw,
)
from app.services.baseline_service import BaselineService
from app.services.change_request_service import (
    ChangeRequestService,
    ChangeRequestView,
    baseline_requirement_ids,
    facts_of,
    predict_for,
)
from app.services.errors import (
    BaselineNotFoundError,
    ChangeRequestBaselineMissingError,
    ChangeRequestInvalidTransitionError,
    ChangeRequestNotFoundError,
    ChangeRequestSummaryNotFoundError,
    ChangeRequestTargetUnknownError,
    DesignStagesNotAvailableError,
)

_AT = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


def _t(*requirement_ids: str, kind: str = "content") -> list[dict[str, str]]:
    """対象の本文・保存の形(要件 ID と変更の種類)。"""
    return [{"requirement_id": r, "kind": kind} for r in requirement_ids]


def _create(**overrides: object) -> ChangeRequestCreate:
    values: dict[str, object] = {
        "title": "一覧に絞り込みを足す",
        "content": "予約の一覧を日付で絞り込めるようにする",
        "background_source": "meeting",
        "background_date": "2026-10-18",
        "background_memo": "定例で依頼",
        "targets": _t("FR-002"),
    }
    values.update(overrides)
    return ChangeRequestCreate.model_validate(values)


def _decision(decision: str, **overrides: object) -> ChangeRequestDecision:
    values: dict[str, object] = {
        "decision": decision,
        "decided_at": _AT,
        "counterpart": "山田様",
        "memo": "打ち合わせ",
        "reason": "費用が合う",
    }
    values.update(overrides)
    return ChangeRequestDecision.model_validate(values)


async def _project_with_baseline(session: AsyncSession) -> Project:
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    return project


def _read(view: ChangeRequestView) -> ChangeRequestRead:
    return ChangeRequestRead.build(
        view.change_request,
        overlapping=view.overlapping,
        derived_from_number=view.derived_from_number,
        derived_numbers=view.derived_numbers,
        allowed_actions=view.allowed_actions,
    )


async def test_create_records_fields_baseline_and_first_event(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)

    view = await ChangeRequestService(db_session).create(
        project,
        _create(
            targets=[*_t(" FR-002"), {"requirement_id": "FR-002", "proposed_text": "日付で絞る"}]
        ),
        created_by=project.user_id,
    )
    read = _read(view)

    assert (read.label, read.status, read.status_label) == ("CR-1", "draft", "下書き")
    assert (read.baseline_label, read.background_source_label) == ("B-1", "打ち合わせ")
    assert read.background_date == date(2026, 10, 18)
    assert [(t.requirement_id, t.kind, t.kind_label, t.proposed_text) for t in read.targets] == [
        ("FR-002", "content", "内容の変更", "日付で絞る")
    ]
    assert view.change_request.targets == [
        {"requirement_id": "FR-002", "kind": "content", "proposed_text": "日付で絞る"}
    ]
    assert read.decision is None
    assert [(e.action, e.from_status, e.to_status) for e in read.events] == [
        ("create", None, "draft")
    ]
    assert read.events[0].actor_id == project.user_id
    assert read.allowed_actions == ["edit", "explain", "withdraw"]


async def test_create_conditions(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    service = ChangeRequestService(db_session)

    with pytest.raises(ChangeRequestBaselineMissingError):
        await service.create(project, _create(), created_by=None)
    await BaselineService(db_session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    with pytest.raises(BaselineNotFoundError):
        await service.create(project, _create(baseline_number=2), created_by=None)
    with pytest.raises(ChangeRequestTargetUnknownError, match="FR-009"):
        await service.create(project, _create(targets=_t("FR-009")), created_by=None)
    with pytest.raises(ChangeRequestTargetUnknownError):
        await service.create(project, _create(targets=_t()), created_by=None)

    # 新しい要件を足す印だけでも作れる
    view = await service.create(
        project, _create(targets=_t(), adds_requirement=True), created_by=None
    )
    assert view.change_request.adds_requirement is True

    project.mode = "simple"
    with pytest.raises(DesignStagesNotAvailableError):
        await service.create(project, _create(), created_by=None)
    # 読み出しと CR ごとの操作も詳細設計モードだけ
    with pytest.raises(DesignStagesNotAvailableError):
        await service.list_for_project(project)
    with pytest.raises(DesignStagesNotAvailableError):
        await service.get(project, view.change_request.number)
    with pytest.raises(DesignStagesNotAvailableError):
        await service.explanation(project, view.change_request.number)


async def test_base_baseline_defaults_to_latest(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)
    await BaselineService(db_session).create(
        project, BaselineCreate(agreed_at=_AT, reason="二回目"), recorded_by=None
    )
    service = ChangeRequestService(db_session)

    latest = await service.create(project, _create(), created_by=None)
    first = await service.create(project, _create(baseline_number=1), created_by=None)

    assert latest.change_request.baseline.number == 2
    assert first.change_request.baseline.number == 1


async def test_explain_decide_and_timeline(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)

    await service.explain(project, 1, actor_id=project.user_id)
    view = await service.decide(project, 1, _decision("agreed"), actor_id=project.user_id)
    read = _read(view)

    assert read.status == "agreed"
    assert read.decision is not None
    assert (read.decision.counterpart, read.decision.reason, read.decision.recorded_by) == (
        "山田様",
        "費用が合う",
        project.user_id,
    )
    assert read.decision.decided_at == _AT
    assert [e.to_status for e in read.events] == ["draft", "explained", "agreed"]
    with pytest.raises(ChangeRequestInvalidTransitionError):
        await service.withdraw(project, 1, ChangeRequestWithdraw(), actor_id=None)
    with pytest.raises(ChangeRequestInvalidTransitionError):
        await service.update(project, 1, ChangeRequestUpdate(title="x"), actor_id=None)


async def test_decide_requires_explained(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)

    with pytest.raises(ChangeRequestInvalidTransitionError):
        await service.decide(project, 1, _decision("rejected"), actor_id=None)
    with pytest.raises(ChangeRequestNotFoundError):
        await service.explain(project, 9, actor_id=None)


async def test_update_in_explained_returns_to_draft(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)

    draft_edit = await service.update(
        project, 1, ChangeRequestUpdate(title="題を直す"), actor_id=None
    )
    # 下書きの中の直しも、直した項目の名前を時系列に残す。値の変わらない保存は残さない
    assert [(e.action, e.refs) for e in draft_edit.change_request.events] == [
        ("create", None),
        ("edit", {"fields": ["title"]}),
    ]
    same = await service.update(
        project, 1, ChangeRequestUpdate(title="題を直す", content=None), actor_id=None
    )
    assert len(same.change_request.events) == 2
    await service.explain(project, 1, actor_id=None)
    view = await service.update(
        project,
        1,
        ChangeRequestUpdate.model_validate(
            {
                "targets": [*_t("FR-001"), *_t("FR-002", kind="delete")],
                "background_date": None,
            }
        ),
        actor_id=project.user_id,
    )

    change_request = view.change_request
    assert change_request.title == "題を直す"
    assert targets_from_json(change_request.targets) == [
        Target("FR-001"),
        Target("FR-002", "delete"),
    ]
    assert change_request.background_date is None
    assert change_request.content == "予約の一覧を日付で絞り込めるようにする"
    assert [(e.action, e.from_status, e.to_status) for e in change_request.events] == [
        ("create", None, "draft"),
        ("edit", "draft", "draft"),
        ("explain", "draft", "explained"),
        ("edit", "explained", "draft"),
    ]
    assert change_request.events[-1].refs == {"fields": ["background_date", "targets"]}
    with pytest.raises(ChangeRequestTargetUnknownError):
        await service.update(
            project, 1, ChangeRequestUpdate.model_validate({"targets": _t("FR-404")}), actor_id=None
        )


async def test_withdraw_keeps_reason(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)

    view = await service.withdraw(
        project, 1, ChangeRequestWithdraw(reason=" 不要になった "), actor_id=None
    )

    assert view.change_request.status == "withdrawn"
    assert view.change_request.events[-1].note == "不要になった"
    assert view.allowed_actions == []


async def test_copy_rejected_makes_new_draft_and_keeps_source(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)
    with pytest.raises(ChangeRequestInvalidTransitionError):
        await service.copy_rejected(project, 1, created_by=None)
    await service.explain(project, 1, actor_id=None)
    await service.decide(project, 1, _decision("rejected", reason="費用が合わない"), actor_id=None)

    copied = _read(await service.copy_rejected(project, 1, created_by=project.user_id))
    source = _read(await service.get(project, 1))

    assert (copied.label, copied.status, copied.derived_from_number) == ("CR-2", "draft", 1)
    assert copied.title == source.title
    assert [(t.requirement_id, t.kind) for t in copied.targets] == [("FR-002", "content")]
    assert copied.decision is None
    assert copied.events[0].action == "copy"
    assert copied.events[0].note == "CR-1(却下)から写した"
    assert source.status == "rejected"
    assert source.decision is not None and source.decision.reason == "費用が合わない"
    assert source.derived_numbers == [2]
    assert (source.events[-1].action, source.events[-1].note) == ("copied", "CR-2 に写した")


async def test_impact_is_predicted_then_saved_on_decision(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    await service.create(
        project,
        _create(targets=[*_t("FR-001"), *_t("FR-002", kind="delete")], adds_requirement=True),
        created_by=None,
    )

    draft = await service.impact(project, 1)
    assert draft.saved is False
    assert draft.change_request.predicted_impact is None
    assert [(t.requirement_id, t.kind) for t in draft.prediction.targets] == [
        ("FR-001", "content"),
        ("FR-002", "delete"),
    ]
    shared = next(i for i in draft.prediction.items if i.key == "screen:SCR-001")
    assert shared.requirement_ids == ("FR-001", "FR-002")
    assert draft.prediction.unpredicted_new_requirement is True
    assert [(e.key, e.state) for e in draft.excluded] == [("test_spec", "absent")]

    await service.explain(project, 1, actor_id=None)
    decided = await service.decide(project, 1, _decision("agreed"), actor_id=None)
    assert decided.change_request.predicted_impact == draft.prediction.to_dict()

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("saved prediction must not be recomputed")

    monkeypatch.setattr("app.services.change_request_service.predict_from_snapshots", _fail)
    agreed = await service.impact(project, 1)
    assert agreed.saved is True
    assert agreed.prediction == draft.prediction


async def test_explanation_follows_the_request_and_prediction(db_session: AsyncSession) -> None:
    """説明書は CR の値・束の写し・予測から組み、CR を直すと md と fingerprint が変わる。判断の
    後も同じ予測(保存したもの)なので、状態の行のほかは変わらない。"""
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    await service.create(
        project,
        _create(
            targets=[{"requirement_id": "FR-002", "proposed_text": "日付で絞り込んで見られる"}]
        ),
        created_by=None,
    )

    draft = await service.explanation(project, 1)
    md = draft.explanation.markdown
    assert md.startswith("# 変更の説明書 CR-1: 一覧に絞り込みを足す")
    assert "| FR-002 | 内容の変更 | 予約の一覧を見られる | 日付で絞り込んで見られる |" in md
    assert "テスト仕様書(無い)" in md
    assert "テスト仕様書が無いため、数えていません" in md

    await service.update(project, 1, ChangeRequestUpdate(content="週で絞り込む"), actor_id=None)
    edited = await service.explanation(project, 1)
    assert edited.explanation.fingerprint != draft.explanation.fingerprint

    await service.explain(project, 1, actor_id=None)
    await service.decide(project, 1, _decision("agreed"), actor_id=None)
    agreed = await service.explanation(project, 1)
    assert "- CR の状態: 合意" in agreed.explanation.markdown
    assert agreed.explanation.body == edited.explanation.body


async def test_summary_generate_edit_confirm_and_stale(db_session: AsyncSession) -> None:
    """要約は未確認ででき出力に載らない。直すと元の文を残して未確認、確かめると載る。CR を直すと
    5節が変わり「古い」になって出力から外れ、確かめ直すとまた載る。作り直すと上書き。"""
    project = await _project_with_baseline(db_session)
    fake = FakeLLM(content=" FR-002 の一覧を日付で絞り込めるようにします。 ")
    service = ChangeRequestService(db_session, llm=fake)
    await service.create(project, _create(), created_by=None)
    with pytest.raises(ChangeRequestSummaryNotFoundError):
        await service.confirm_summary(project, 1, actor_id=None)

    generated = await service.generate_summary(project, 1, user_id=project.user_id)
    summary = generated.summary
    assert summary is not None
    assert (summary.text, summary.generated_text) == (
        "FR-002 の一覧を日付で絞り込めるようにします。",
        "FR-002 の一覧を日付で絞り込めるようにします。",
    )
    assert summary.generated_by == project.user_id
    assert summary.generated_model is not None
    assert generated.summary_state == "unconfirmed"
    assert "## 要約" not in generated.output
    # LLM に渡したのは説明書の5節だけ
    assert fake.invoke_messages[-1][1].content == generated.explanation.markdown

    edited = await service.edit_summary(project, 1, "  一覧を日付で絞り込めるようにします。 ")
    assert edited.summary is not None
    assert edited.summary.text == "一覧を日付で絞り込めるようにします。"
    assert edited.summary.generated_text == "FR-002 の一覧を日付で絞り込めるようにします。"
    assert edited.summary.edited_at is not None

    confirmed = await service.confirm_summary(project, 1, actor_id=project.user_id)
    assert confirmed.summary_state == "confirmed"
    assert confirmed.summary is not None and confirmed.summary.confirmed_by == project.user_id
    assert "## 要約\n\n一覧を日付で絞り込めるようにします。\n\n## 1." in confirmed.output

    await service.update(project, 1, ChangeRequestUpdate(content="週で絞り込む"), actor_id=None)
    stale = await service.explanation(project, 1)
    assert stale.summary_state == "stale"
    assert "## 要約" not in stale.output
    again = await service.confirm_summary(project, 1, actor_id=None)
    assert again.summary_state == "confirmed"

    regenerated = await service.generate_summary(project, 1, user_id=project.user_id)
    assert regenerated.summary is not None and regenerated.summary.confirmed_at is None
    assert regenerated.summary_state == "unconfirmed"


async def test_summary_edit_requires_summary(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)

    with pytest.raises(ChangeRequestSummaryNotFoundError):
        await service.edit_summary(project, 1, "要約")
    assert (await service.explanation(project, 1)).summary_state is None


def test_facts_of_reads_the_row() -> None:
    row = SimpleNamespace(
        number=3,
        title="題",
        status="draft",
        content="内容",
        background_source="email",
        background_date=None,
        background_memo="",
        targets=[{"requirement_id": "FR-001", "kind": "delete"}],
        adds_requirement=True,
    )

    facts = facts_of(row, 2)  # type: ignore[arg-type]

    assert (facts.number, facts.baseline_number, facts.adds_requirement) == (3, 2, True)
    assert facts.targets == (Target("FR-001", "delete"),)


async def test_withdrawn_impact_is_not_saved(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)
    await service.withdraw(project, 1, ChangeRequestWithdraw(), actor_id=None)

    view = await service.impact(project, 1)

    assert view.saved is False
    assert view.change_request.predicted_impact is None
    assert predict_for(view.change_request, view.change_request.baseline) == view.prediction
    with pytest.raises(ChangeRequestNotFoundError):
        await service.impact(project, 2)


async def test_overlapping_active_requests(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)
    await service.create(project, _create(targets=_t("NFR-001")), created_by=None)
    await service.create(
        project, _create(targets=_t("FR-001", "FR-002")), created_by=None
    )

    third = _read(await service.get(project, 3))
    assert [(o.label, o.requirement_ids) for o in third.overlapping] == [("CR-1", ["FR-002"])]

    await service.withdraw(project, 1, ChangeRequestWithdraw(), actor_id=None)
    assert (await service.get(project, 3)).overlapping == []
    assert (await service.get(project, 1)).overlapping == []  # 終わった CR は重なりを見ない


async def test_list_is_newest_first(db_session: AsyncSession) -> None:
    project = await _project_with_baseline(db_session)
    service = ChangeRequestService(db_session)
    for title in ("一つ目", "二つ目"):
        await service.create(project, _create(title=title), created_by=None)

    listed = [
        ChangeRequestSummaryRead.from_model(c) for c in await service.list_for_project(project)
    ]

    assert [(c.label, c.title) for c in listed] == [("CR-2", "二つ目"), ("CR-1", "一つ目")]
    assert not hasattr(listed[0], "events")


async def test_baseline_requirement_ids_reads_the_snapshot(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    baseline = await BaselineService(db_session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )

    assert baseline_requirement_ids(baseline) == ["FR-001", "FR-002", "NFR-001"]


def test_payloads_validate() -> None:
    assert _create(title="  題 ").title == "題"
    with pytest.raises(ValidationError):
        _create(background_source="phone")
    with pytest.raises(ValidationError):
        _create(content=" ")
    with pytest.raises(ValidationError):
        _decision("pending")
    with pytest.raises(ValidationError):
        _decision("agreed", decided_at=datetime(2026, 10, 19, 10, 0))  # タイムゾーンなし
    assert _decision("agreed", counterpart=" ").counterpart is None
    assert isinstance(_decision("agreed"), AgreementNotes)
    assert issubclass(BaselineCreate, AgreementNotes)
    # 対象: 空白・同じ組の重複は除き、同じ要件を違う種類で指定すると検証の誤り
    targets = _create(targets=[*_t(" FR-001", "FR-001"), *_t("FR-002", kind="delete")]).targets
    assert [(t.requirement_id, t.kind) for t in targets] == [
        ("FR-001", "content"),
        ("FR-002", "delete"),
    ]
    with pytest.raises(ValidationError, match="FR-001"):
        _create(targets=[*_t("FR-001"), *_t("FR-001", kind="delete")])
    with pytest.raises(ValidationError):
        _create(targets=_t("FR-001", kind="rename"))
    assert ChangeRequestUpdate().targets is None
    # 変更後の案: 前後の空白を除く。削除の対象には書けない
    proposed = _create(
        targets=[{"requirement_id": "FR-001", "proposed_text": " 5件まで添付できる "}]
    ).targets
    assert [(t.requirement_id, t.proposed_text) for t in proposed] == [
        ("FR-001", "5件まで添付できる")
    ]
    with pytest.raises(ValidationError, match="削除の対象"):
        _create(targets=[{"requirement_id": "FR-001", "kind": "delete", "proposed_text": "案"}])
