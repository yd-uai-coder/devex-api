"""合意済みの要素を、再生成の後に合意の形へ戻す(純粋関数)。

docs/external_design.md 2.15節「合意済みの行の保護」。LLM には合意済みの行を変えないよう頼むが、
約束だけでは揺れを止められないので、組み立ての後にコードで戻す。

- 要件の行(`req:<ID>`): 中身(すべての欄)が合意と違えば合意の中身に置き換え、落ちていれば戻す。
  戻す位置は、前の版で同じ種別の直前にあった行の後(並びが再生成の前後で変わらないように)。
- 利用者と使う場面(`users`): 1.2 全体を1つの要素として比べ、違えば合意の形に置き換える。行の
  ID は合意した中身の ID を使い、無ければ(ID を振る前の合意)今の版・前の版の同じ名前の行から
  引き継ぐ。
- 合意済みの行と同じ文の新しい行(言い換えずに ID だけ変えた重複)は除く。
- 画面(`screen:<ID>`)・API(`api:<メソッド> <パス>`)・外部連携(`integration:<サービス名>`):
  要件の行と同じく、すべての欄を比べて置き換え、落ちていれば前の版の位置に戻す。合意済みの画面と
  同じ名前の新しい画面(ID だけを付け直した重複)と、合意済みの外部連携と正規化したサービス名
  (`service_match_key`)が同じ新しい外部連携(「X API」を「X」と言い換えた重複)は除く。
- 書き換えてよい要素(反映する CR の対象の要件・保存した予測の画面と API)は`exempt`で受け、戻さない。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple, get_args

from pydantic import BaseModel

from app.agreements.items import (
    USERS_KEY,
    AgreedKind,
    body_of,
    integration_key,
    service_match_key,
)
from app.detailed_design.api_list import endpoint_key
from app.documents.external_design.model import (
    SCREEN_PREFIX,
    ApiRow,
    ExternalDesignModel,
    IntegrationRow,
    ScreenRow,
)
from app.documents.ids import parse_seq_id
from app.documents.requirements.model import (
    REQUIREMENT_KINDS,
    USER_PREFIX,
    RequirementRow,
    RequirementsModel,
    UserScene,
    number_users,
)

RestoreReason = Literal["changed", "dropped"]

RESTORE_REASONS: tuple[RestoreReason, ...] = get_args(RestoreReason)

_REQ_PREFIX = "req:"
_SCREEN_PREFIX = "screen:"
_API_PREFIX = "api:"
_INTEGRATION_PREFIX = "integration:"


@dataclass(frozen=True)
class Restored:
    """戻した要素1つ(`changed` = LLM が書き換えた、`dropped` = LLM が落とした)。"""

    key: str
    reason: RestoreReason


def requirement_key(req_id: str) -> str:
    return f"{_REQ_PREFIX}{req_id}"


def agreed_requirement_ids(agreed: Mapping[str, Any], exempt: frozenset[str]) -> frozenset[str]:
    """守る要件の ID(合意済みで、書き換えてよい要素に入らないもの)。"""
    return frozenset(
        key.removeprefix(_REQ_PREFIX)
        for key in agreed
        if key.startswith(_REQ_PREFIX) and key not in exempt
    )


def protect_requirements(
    model: RequirementsModel,
    previous: RequirementsModel | None,
    agreed: Mapping[str, dict[str, Any]],
    *,
    exempt: frozenset[str] = frozenset(),
) -> tuple[RequirementsModel, list[Restored]]:
    """組み立てた model の、合意済みの要件の行と利用者(1.2)を合意の形に戻す。

    `agreed`は今の合意(キー → 合意した中身)。要件と利用者のキーだけを読む。戻した要素を、
    要件(合意の順)→ 利用者の順に返す。合意が無ければ model をそのまま返す。"""
    rows, restored, dropped = _protect_rows(
        model.rows,
        previous.rows if previous else [],
        _agreed_rows(agreed, _REQ_PREFIX, RequirementRow, exempt),
        "requirement",
        key_of=lambda row: requirement_key(row.id),
        group_of=lambda row: row.kind,
        duplicate_of=lambda row: row.text.strip(),
    )

    users, next_user_seq = model.users, model.next_seq
    agreed_users = agreed.get(USERS_KEY)
    if agreed_users is not None and USERS_KEY not in exempt:
        current = {"users": [u.model_dump(mode="json") for u in model.users]}
        if body_of("users", current) != body_of("users", agreed_users):
            users, next_user_seq = number_users(
                [UserScene.model_validate(u) for u in agreed_users["users"]],
                model.next_seq,
                [*model.users, *(previous.users if previous else [])],
            )
            restored.append(Restored(USERS_KEY, "dropped" if not model.users else "changed"))

    if not restored and len(rows) == len(model.rows):
        return model, restored
    next_seq = _next_seq(model.next_seq, [row.id for row in dropped], REQUIREMENT_KINDS)
    if USER_PREFIX in next_user_seq:
        next_seq[USER_PREFIX] = next_user_seq[USER_PREFIX]
    update = {"rows": rows, "users": users, "next_seq": next_seq}
    return model.model_copy(update=update), restored


class AgreedExternalIds(NamedTuple):
    """守る外部設計書の要素(プロンプトの「合意済み」の印に使う)。画面は画面ID、API は
    `endpoint_key`、外部連携はサービス名(文書の側はキーの形を知らないので、行の値で渡す)。"""

    screen_ids: frozenset[str]
    endpoints: frozenset[str]
    services: frozenset[str]


def agreed_external_ids(agreed: Mapping[str, Any], exempt: frozenset[str]) -> AgreedExternalIds:
    """合意済みの画面・API・外部連携のうち、書き換えてよい要素に入らないもの。"""
    keys = [key for key in agreed if key not in exempt]

    def ids(prefix: str) -> frozenset[str]:
        return frozenset(key.removeprefix(prefix) for key in keys if key.startswith(prefix))

    return AgreedExternalIds(
        ids(_SCREEN_PREFIX), ids(_API_PREFIX), ids(_INTEGRATION_PREFIX)
    )


def protect_external_design(
    model: ExternalDesignModel,
    previous: ExternalDesignModel | None,
    agreed: Mapping[str, dict[str, Any]],
    *,
    exempt: frozenset[str] = frozenset(),
) -> tuple[ExternalDesignModel, list[Restored]]:
    """組み立てた model の、合意済みの画面・API・外部連携を合意の形に戻す。

    概要・画面遷移・データ入出力は合意の対象ではないので触らない。戻した要素を、画面 → API →
    外部連携(それぞれ合意の順)で返す。合意が無ければ model をそのまま返す。"""
    screens, restored_screens, dropped_screens = _protect_rows(
        model.screens,
        previous.screens if previous else [],
        _agreed_rows(agreed, _SCREEN_PREFIX, ScreenRow, exempt),
        "screen",
        key_of=_screen_key,
        duplicate_of=lambda row: row.name.strip(),
    )
    apis, restored_apis, _ = _protect_rows(
        model.apis,
        previous.apis if previous else [],
        _agreed_rows(agreed, _API_PREFIX, ApiRow, exempt),
        "api",
        key_of=_api_key,
    )
    integrations, restored_integrations, _ = _protect_rows(
        model.integrations,
        previous.integrations if previous else [],
        _agreed_rows(agreed, _INTEGRATION_PREFIX, IntegrationRow, exempt),
        "integration",
        key_of=lambda row: integration_key(row.service),
        duplicate_of=lambda row: service_match_key(row.service),
    )
    restored = restored_screens + restored_apis + restored_integrations
    if not restored and (
        len(screens),
        len(apis),
        len(integrations),
    ) == (len(model.screens), len(model.apis), len(model.integrations)):
        return model, restored
    next_seq = _next_seq(model.next_seq, [row.id for row in dropped_screens], (SCREEN_PREFIX,))
    update = {
        "screens": screens,
        "apis": apis,
        "integrations": integrations,
        "next_seq": next_seq,
    }
    return model.model_copy(update=update), restored


def _screen_key(row: ScreenRow) -> str:
    return f"{_SCREEN_PREFIX}{row.id}"


def _api_key(row: ApiRow) -> str:
    return f"{_API_PREFIX}{endpoint_key(row.method, row.path)}"


def _agreed_rows[Row: BaseModel](
    agreed: Mapping[str, dict[str, Any]], prefix: str, row_type: type[Row], exempt: frozenset[str]
) -> dict[str, Row]:
    """今の合意のうち、守る種類の要素を行の model に読み直したもの(キー → 行。合意の順)。"""
    return {
        key: row_type.model_validate(content)
        for key, content in agreed.items()
        if key.startswith(prefix) and key not in exempt
    }


def _protect_rows[Row: BaseModel](
    rows: Sequence[Row],
    previous: Sequence[Row],
    agreed_rows: Mapping[str, Row],
    kind: AgreedKind,
    *,
    key_of: Callable[[Row], str],
    group_of: Callable[[Row], str] = lambda _: "",
    duplicate_of: Callable[[Row], str] | None = None,
) -> tuple[list[Row], list[Restored], list[Row]]:
    """1つの一覧の合意済みの行を合意の形に戻す(要件・画面・API・外部連携で共有)。

    すべての欄が合意と違う行は置き換え、無い行は前の版の位置に戻す。`duplicate_of`があれば、
    合意済みの行と同じ値(要件の文・画面の名前・正規化したサービス名)を持つ合意の無い行を除く。戻した要素の一覧と、
    戻した(落ちていた)行も返す。"""
    out = list(rows)
    index: dict[str, int] = {}
    for i, row in enumerate(out):
        index.setdefault(key_of(row), i)
    restored: list[Restored] = []
    dropped: list[Row] = []
    for key, row in agreed_rows.items():
        at = index.get(key)
        if at is None:
            dropped.append(row)
            restored.append(Restored(key, "dropped"))
        elif body_of(kind, out[at].model_dump(mode="json")) != body_of(
            kind, row.model_dump(mode="json")
        ):
            out[at] = row
            restored.append(Restored(key, "changed"))
    if duplicate_of is not None:
        values = {duplicate_of(row) for row in agreed_rows.values()}
        out = [row for row in out if key_of(row) in agreed_rows or duplicate_of(row) not in values]
    for row in _in_previous_order(dropped, previous, key_of):
        out.insert(_insert_at(out, row, previous, key_of, group_of), row)
    return out, restored, dropped


def _in_previous_order[Row: BaseModel](
    rows: list[Row], previous: Sequence[Row], key_of: Callable[[Row], str]
) -> list[Row]:
    """前の版の並びの順(前の版に無い行は後ろ。その中は合意の順)。続けて戻す行が、前に戻した
    行の後ろにつながるように。"""
    order = {key_of(row): i for i, row in enumerate(previous)}
    return sorted(rows, key=lambda row: order.get(key_of(row), len(order)))


def _insert_at[Row: BaseModel](
    rows: list[Row],
    row: Row,
    previous: Sequence[Row],
    key_of: Callable[[Row], str],
    group_of: Callable[[Row], str],
) -> int:
    """落ちた行を戻す位置。前の版で同じ種別の直前にあった行(今もあるもの)の後。前の版で同じ種別の
    先頭なら、今の同じ種別の先頭。前の版に無ければ同じ種別の末尾。同じ種別が無ければ末尾。
    種別(`group_of`)は要件の FR / NFR / CON で、画面・API・外部連携は1つの種別とみなす。"""
    present = {key_of(r): i for i, r in enumerate(rows)}
    group = group_of(row)
    same_group = [i for i, r in enumerate(rows) if group_of(r) == group]
    keys = [key_of(r) for r in previous if group_of(r) == group]
    key = key_of(row)
    if key in keys:
        for prev in reversed(keys[: keys.index(key)]):
            if prev in present:
                return present[prev] + 1
        return same_group[0] if same_group else len(rows)
    return same_group[-1] + 1 if same_group else len(rows)


def _next_seq(
    next_seq: Mapping[str, int], restored_ids: list[str], prefixes: Sequence[str]
) -> dict[str, int]:
    """戻した行の番号を、次に振る番号が越えるようにする(消えた ID を使い回さないため)。"""
    seq = dict(next_seq)
    for row_id in restored_ids:
        parsed = parse_seq_id(row_id, prefixes)
        if parsed is not None:
            kind, number = parsed
            seq[kind] = max(seq.get(kind, 1), number + 1)
    return seq
