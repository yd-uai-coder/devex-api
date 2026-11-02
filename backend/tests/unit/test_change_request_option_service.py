"""変更要求(CR)の選択肢(案)の保存・合意で選ぶ案・反映の指示・写し・たどる記録と、LLM の下書きの
作成・取り込みのテスト。

SUT: ChangeRequestService(create / update / decide / copy_rejected / reflection / trace の
     案の扱い)、
     純粋関数 checked_options / chosen_option / chosen_option_name、スキーマ
     ChangeRequestOptionsIn・ChangeRequestDecision(`option`)・ChangeRequestRead(`options`・
     `decision.option`)、モデル ChangeRequest(`options`・`decision_option`)と
     ChangeRequestRepository.create(`options`)。下書きは ChangeRequestService(
     generate_option_draft / option_draft / adopt_option_draft)・OptionDraftView、
     ChangeRequestOptionDraftRepository(get_for / replace)と ChangeRequestOptionDraft の ORM 定義
ドライバ: pytest のテスト関数
スタブ: 永続化と遷移が検証対象のため、テスト用の SQLite のセッションと、要件 FR-001・FR-002・
NFR-001 と承認済みの段階を持つ fixture(`create_impact_project`)から作った合意済みの版を使う。
下書きの LLM は、決まった構造化出力を返すフェイク(FakeLLM)をサービスに注入する
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.fake_llm import FakeLLM

from app.change_requests import (
    OptionDraftItemOut,
    OptionDraftOut,
    OptionDraftTargetOut,
    Prediction,
    Target,
    targets_from_json,
)
from app.models.change_request import ChangeRequest
from app.models.change_request_option_draft import ChangeRequestOptionDraft
from app.models.project import Project
from app.repositories.change_request_option_draft import ChangeRequestOptionDraftRepository
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestOptionsIn,
    ChangeRequestRead,
    ChangeRequestUpdate,
)
from app.services.baseline_service import BaselineService
from app.services.change_request_service import (
    ChangeRequestService,
    ChangeRequestView,
    checked_options,
    chosen_option,
    chosen_option_name,
)
from app.services.errors import (
    ChangeRequestInvalidTransitionError,
    ChangeRequestOptionDraftNotFoundError,
    ChangeRequestOptionInvalidError,
)

_AT = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


def _options(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "items": [
            {
                "name": "登録だけ",
                "targets": [{"requirement_id": "FR-001", "proposed_text": "登録で確認する"}],
                "outcome": "手間が小さい。一覧はそのまま",
            },
            {
                "name": "登録と一覧",
                "targets": [{"requirement_id": "FR-001"}, {"requirement_id": "FR-002"}],
                "outcome": "一覧も直すので手間が増える",
            },
        ],
        "recommended": 1,
        "reason": "早く出せる",
    }
    values.update(overrides)
    return values


def _create(**overrides: object) -> ChangeRequestCreate:
    values: dict[str, object] = {
        "title": "登録の確認を足す",
        "content": "予約の登録の前に確認を挟む",
        "background_source": "meeting",
        "targets": [{"requirement_id": "FR-001"}, {"requirement_id": "FR-002"}],
        "options": _options(),
    }
    values.update(overrides)
    return ChangeRequestCreate.model_validate(values)


def _decision(decision: str = "agreed", **overrides: object) -> ChangeRequestDecision:
    values: dict[str, object] = {"decision": decision, "decided_at": _AT, "reason": "合う"}
    values.update(overrides)
    return ChangeRequestDecision.model_validate(values)


def _read(view: ChangeRequestView) -> ChangeRequestRead:
    return ChangeRequestRead.build(
        view.change_request,
        overlapping=view.overlapping,
        derived_from_number=view.derived_from_number,
        derived_numbers=view.derived_numbers,
        allowed_actions=view.allowed_actions,
    )


async def _project(session: AsyncSession) -> Project:
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    return project


async def test_create_keeps_options_and_reads_them(db_session: AsyncSession) -> None:
    """作成で案を保存し、詳細に番号・呼び名・対象・おすすめを出す。"""
    project = await _project(db_session)

    read = _read(await ChangeRequestService(db_session).create(project, _create(), created_by=None))

    assert read.options is not None
    assert [(o.number, o.label, o.name, o.recommended) for o in read.options.items] == [
        (1, "案1", "登録だけ", True),
        (2, "案2", "登録と一覧", False),
    ]
    assert read.options.items[0].targets[0].proposed_text == "登録で確認する"
    assert read.options.reason == "早く出せる"


async def test_create_without_options_is_unchanged(db_session: AsyncSession) -> None:
    """案を送らない CR は、今までどおり案なし(列は NULL)。"""
    project = await _project(db_session)

    view = await ChangeRequestService(db_session).create(
        project, _create(options=None), created_by=None
    )

    assert view.change_request.options is None
    assert _read(view).options is None


def test_schema_rejects_option_rules() -> None:
    """案の数・おすすめの規則に合わなければ検証の誤り(422)。"""
    with pytest.raises(ValidationError, match="2〜3件"):
        ChangeRequestOptionsIn.model_validate(
            {"items": [{"name": "A", "targets": [], "outcome": "x"}]}
        )
    with pytest.raises(ValidationError, match="理由"):
        _create(options=_options(reason=""))


async def test_option_targets_must_be_in_the_change_request(db_session: AsyncSession) -> None:
    """案の対象が CR の対象に無ければ ChangeRequestOptionInvalidError。"""
    project = await _project(db_session)

    with pytest.raises(ChangeRequestOptionInvalidError, match="案2の対象"):
        await ChangeRequestService(db_session).create(
            project, _create(targets=[{"requirement_id": "FR-001"}]), created_by=None
        )
    with pytest.raises(ChangeRequestOptionInvalidError):
        checked_options(
            ChangeRequestOptionsIn.model_validate(_options()).to_options(),
            [Target("FR-002")],
            False,
        )


async def test_update_options_and_recheck_on_targets(db_session: AsyncSession) -> None:
    """案を直すと時系列に「選択肢」を残し、説明済みなら下書きに戻る。対象を変えると、今の案を
    確かめ直す(案の対象が外れたら断る)。案を送らなければ変えず、空の`items`で消える。"""
    project = await _project(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)
    await service.explain(project, 1, actor_id=None)

    view = await service.update(
        project,
        1,
        ChangeRequestUpdate.model_validate({"options": _options(recommended=2)}),
        actor_id=None,
    )
    assert view.change_request.status == "draft"
    assert view.change_request.events[-1].refs == {"fields": ["options"]}
    assert (view.change_request.options or {})["recommended"] == 2

    with pytest.raises(ChangeRequestOptionInvalidError):
        await service.update(
            project,
            1,
            ChangeRequestUpdate.model_validate({"targets": [{"requirement_id": "FR-001"}]}),
            actor_id=None,
        )

    view = await service.update(project, 1, ChangeRequestUpdate(title="題"), actor_id=None)
    assert view.change_request.options is not None
    view = await service.update(
        project, 1, ChangeRequestUpdate.model_validate({"options": {"items": []}}), actor_id=None
    )
    assert view.change_request.options is None


async def test_agree_with_option_replaces_targets(db_session: AsyncSession) -> None:
    """案のある CR の合意は案を選ぶ。選んだ案の対象が CR の対象になり、予測はその対象で保存する。
    前の対象と選んだ番号は時系列の行に残す。"""
    project = await _project(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)
    await service.explain(project, 1, actor_id=None)

    with pytest.raises(ChangeRequestOptionInvalidError, match="選んで"):
        await service.decide(project, 1, _decision(), actor_id=None)
    with pytest.raises(ChangeRequestOptionInvalidError, match="案3"):
        await service.decide(project, 1, _decision(option=3), actor_id=None)

    view = await service.decide(project, 1, _decision(option=1), actor_id=None)
    change_request: ChangeRequest = view.change_request

    assert change_request.decision_option == 1
    assert targets_from_json(change_request.targets) == [
        Target("FR-001", "content", "登録で確認する")
    ]
    assert change_request.events[-1].refs == {
        "option": 1,
        "targets": [
            {"requirement_id": "FR-001", "kind": "content", "proposed_text": ""},
            {"requirement_id": "FR-002", "kind": "content", "proposed_text": ""},
        ],
    }
    saved = Prediction.from_dict(change_request.predicted_impact or {})
    assert [t.requirement_id for t in saved.targets] == ["FR-001"]
    decision = _read(view).decision
    assert decision is not None and decision.option == 1
    assert chosen_option_name(change_request) == "案1 登録だけ"


async def test_option_choice_only_for_agreeing_with_options(db_session: AsyncSession) -> None:
    """却下と案の無い CR では案を選べない。"""
    project = await _project(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)
    await service.create(project, _create(options=None), created_by=None)
    for number in (1, 2):
        await service.explain(project, number, actor_id=None)

    with pytest.raises(ChangeRequestOptionInvalidError, match="案を選べる"):
        await service.decide(project, 1, _decision("rejected", option=1), actor_id=None)
    with pytest.raises(ChangeRequestOptionInvalidError, match="案を選べる"):
        await service.decide(project, 2, _decision(option=1), actor_id=None)

    rejected = await service.decide(project, 1, _decision("rejected"), actor_id=None)
    assert rejected.change_request.decision_option is None
    assert chosen_option(rejected.change_request, _decision("rejected")) is None


async def test_copy_rejected_copies_options(db_session: AsyncSession) -> None:
    """却下した CR の写しは案も写す。"""
    project = await _project(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)
    await service.explain(project, 1, actor_id=None)
    await service.decide(project, 1, _decision("rejected"), actor_id=None)

    source = (await service.get(project, 1)).change_request.options
    copied = await service.copy_rejected(project, 1, created_by=None)

    assert source is not None
    assert copied.change_request.options == source


async def test_reflection_and_trace_name_the_agreed_option(db_session: AsyncSession) -> None:
    """反映の指示とたどる記録の合意の行に、選んだ案を出す。"""
    project = await _project(db_session)
    service = ChangeRequestService(db_session)
    await service.create(project, _create(), created_by=None)
    await service.explain(project, 1, actor_id=None)
    await service.decide(project, 1, _decision(option=2), actor_id=None)

    reflection = await service.reflection(project.id, 1)
    trace = await service.trace(project, 1)

    assert reflection is not None
    assert "合意した案: 案2 登録と一覧(結果: 一覧も直すので手間が増える)" in reflection.instruction
    assert "- FR-002(内容の変更)" in reflection.instruction
    agree = next(e for e in trace.entries if e.action == "agree")
    assert agree.note.startswith("選んだ案: 案2 登録と一覧")


def _draft_llm(*target_sets: tuple[str, ...]) -> FakeLLM:
    return FakeLLM(
        structured=OptionDraftOut(
            options=[
                OptionDraftItemOut(
                    name=f"案の{i}",
                    targets=[OptionDraftTargetOut(requirement_id=r) for r in ids],
                    outcome="結果の文",
                )
                for i, ids in enumerate(target_sets, start=1)
            ],
            recommended=1,
            reason="早い",
        )
    )


async def test_generate_and_adopt_option_draft(db_session: AsyncSession) -> None:
    """下書きは未確認ででき、照合の結果を持つ。取り込むと案が CR に写り(説明済みは下書きに戻る)、
    下書きは取り込み済みになる。作り直すと上書きし、取り込んだ記録は消える。"""
    project = await _project(db_session)
    service = ChangeRequestService(
        db_session, llm=_draft_llm(("FR-001",), ("FR-001", "FR-002"))
    )
    await service.create(project, _create(options=None), created_by=None)
    await service.explain(project, 1, actor_id=None)

    with pytest.raises(ChangeRequestOptionDraftNotFoundError):
        await service.option_draft(project, 1)
    view = await service.generate_option_draft(project, 1, user_id=project.user_id)
    assert view.draft.status == "unconfirmed"
    assert [o.name for o in view.options.items] == ["案の1", "案の2"]
    assert view.check.ok
    assert view.current_fingerprint == view.draft.fingerprint

    adopted = await service.adopt_option_draft(project, 1, actor_id=project.user_id)
    assert adopted.change_request.status == "draft"
    assert adopted.change_request.events[-1].refs == {"fields": ["options"]}
    assert [o["name"] for o in (adopted.change_request.options or {})["items"]] == [
        "案の1",
        "案の2",
    ]
    after = await service.option_draft(project, 1)
    assert (after.draft.status, after.draft.adopted_by) == ("adopted", project.user_id)
    assert after.current_fingerprint != after.draft.fingerprint  # 説明書に選択肢の節が入った

    again = await service.generate_option_draft(project, 1, user_id=project.user_id)
    assert (again.draft.status, again.draft.adopted_at) == ("unconfirmed", None)
    rows = await ChangeRequestOptionDraftRepository(db_session).list_all()
    assert len(rows) == 1 and isinstance(rows[0], ChangeRequestOptionDraft)


async def test_option_draft_that_breaks_rules_is_not_adopted(db_session: AsyncSession) -> None:
    """CR の対象に無い要件を変える下書きは、取り込めない(問題を返す)。"""
    project = await _project(db_session)
    service = ChangeRequestService(db_session, llm=_draft_llm(("FR-001",), ("NFR-001",)))
    await service.create(project, _create(options=None), created_by=None)

    view = await service.generate_option_draft(project, 1, user_id=project.user_id)

    assert view.check.problems == ("案2の対象が CR の対象にありません: NFR-001",)
    with pytest.raises(ChangeRequestOptionInvalidError, match="NFR-001"):
        await service.adopt_option_draft(project, 1, actor_id=None)


async def test_option_draft_only_before_the_decision(db_session: AsyncSession) -> None:
    """合意・却下の後は下書きを作れない(案を直せないため)。"""
    project = await _project(db_session)
    service = ChangeRequestService(db_session, llm=_draft_llm(("FR-001",), ("FR-002",)))
    await service.create(project, _create(options=None), created_by=None)
    await service.explain(project, 1, actor_id=None)
    await service.decide(project, 1, _decision("rejected"), actor_id=None)

    with pytest.raises(ChangeRequestInvalidTransitionError):
        await service.generate_option_draft(project, 1, user_id=project.user_id)
