"""ヒアリングの観点の表(純粋関数)。

docs/internal_design.md 3.3節「観点の定義はコードに持つ」。観点は機能面の観点と、IPA の
非機能要求グレードの6大項目。規模(個人・小規模 / 社内 / 公開サービス)で必須を切り替える。

完了の判定はここで決定的に行う(LLM には判定させない)。LLM は発言から観点ごとの状態と根拠を
返すだけで、その取り込み(`merge_judgement`)もここで行う。根拠がユーザー側の発言に結べない
「決定」は取り込まない(AI の提案・推測を、利用者が決めたことと数えないため)。「採用」と、
チャットで確かめる観点(規模・技術スタック)は、利用者の発言(初期入力・添付でない)が根拠の
ときだけ取り込む。
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, get_args

Scale = Literal["personal", "internal", "public"]
PointKind = Literal["function", "nonfunctional"]
PointStatus = Literal["decided", "adopted", "open", "not_applicable"]
PointKey = Literal[
    "scale",
    "purpose",
    "users",
    "daily_flow",
    "core",
    "scope",
    "exceptions",
    "future",
    "tech",
    "schedule",
    "availability",
    "backup",
    "performance",
    "operability",
    "data_retention",
    "migration",
    "security",
    "logging",
    "abuse",
    "environment",
]

POINT_KEYS: tuple[PointKey, ...] = get_args(PointKey)
SCALE_LABEL: dict[Scale, str] = {
    "personal": "個人・小規模",
    "internal": "社内",
    "public": "公開サービス",
}
STATUS_LABEL: dict[PointStatus, str] = {
    "decided": "ユーザーが決定",
    "adopted": "AI の提案を採用",
    "open": "未決",
    "not_applicable": "対象外",
}

# 規模が決まるまで(または規模を対象外としたとき)に必須を数える列
DEFAULT_SCALE: Scale = "personal"


@dataclass(frozen=True)
class PointDef:
    key: PointKey
    kind: PointKind
    label: str
    question: str  # 場面で聞く問いの例
    required: frozenset[Scale]  # 必須になる規模
    # 決まったと言える条件。判定の LLM はこれを満たすときだけ決定・採用にし、返信の LLM は
    # これを満たす答えが返るように問う(一語だけで決まったことにしないため)
    done_when: str
    # 初期入力・添付から推し量らず、チャットで利用者に確かめる観点(規模・技術スタック)。
    # 規模は後の必須を切り替え、技術スタックは推測で埋まると設計の全体が創作になるため
    confirm_in_chat: bool = False
    # IPA の大項目(非機能の観点で、ラベルが大項目と違うとき)。大項目の2つ目以降の側面(データの
    # 保全・ログなど)は別の観点にして、1つの観点に1つの問いで届くようにしている
    category: str = ""

    @property
    def ipa_category(self) -> str:
        """IPA の大項目。`category`が空ならラベル。"""
        return self.category or self.label


_ALL: frozenset[Scale] = frozenset({"personal", "internal", "public"})
_INTERNAL_UP: frozenset[Scale] = frozenset({"internal", "public"})
_PUBLIC: frozenset[Scale] = frozenset({"public"})
_NONE: frozenset[Scale] = frozenset()

# 定義の順 = 画面の行の順 = 返信の LLM に渡す順(規模を最初に聞く)。技術スタックは規模によらず必須。
# 大項目の2つ目以降の側面は、その大項目の観点のすぐ後ろに置く(続けて聞く)
POINT_DEFS: tuple[PointDef, ...] = (
    PointDef(
        key="scale",
        kind="function",
        label="規模",
        question="使うのは自分(と数人)だけですか。社内の人ですか。誰でも登録できるサービスですか",
        required=_ALL,
        done_when="使う人の範囲(自分と数人 / 社内 / 誰でも登録できる)を利用者が選んだ",
        confirm_in_chat=True,
    ),
    PointDef(
        key="purpose",
        kind="function",
        label="目的・課題",
        question="これが無いと、今は何に困っていますか",
        required=_ALL,
        done_when="今困っていること、または作る目的が分かる",
    ),
    PointDef(
        key="users",
        kind="function",
        label="利用者と使う場面",
        question="誰が、どんなときに開きますか",
        required=_ALL,
        done_when="誰が、どんな場面で使うかが分かる",
    ),
    PointDef(
        key="daily_flow",
        kind="function",
        label="利用者の1日の流れ",
        question="使う人の1日のどこで、どのくらいの時間使いますか",
        required=_INTERNAL_UP,
        done_when="1日のどのタイミングで、どのくらいの時間使うかが分かる",
    ),
    PointDef(
        key="core",
        kind="function",
        label="コア機能",
        question="最初の版で、これだけはできてほしいことは何ですか",
        required=_ALL,
        done_when="最初の版で必ずできてほしい機能が挙がっている",
    ),
    PointDef(
        key="scope",
        kind="function",
        label="MVP の範囲",
        question="最初の版で作らないと決めているものはありますか",
        required=_ALL,
        done_when="最初の版で作らないもの(無いなら、無いこと)が分かる",
    ),
    PointDef(
        key="exceptions",
        kind="function",
        label="例外の場面",
        question=(
            "入力を間違えた・取り消したい・途中で失敗した・"
            "権限が無い、のときにどうなってほしいですか"
        ),
        required=_ALL,
        done_when="失敗したとき・入力を誤ったとき・やり直したいときの、どれかの動きが分かる",
    ),
    PointDef(
        key="future",
        kind="function",
        label="将来の拡張",
        question="後で足したいと思っているものはありますか",
        required=_PUBLIC,
        done_when="後で足したいもの(無いなら、無いこと)が分かる",
    ),
    PointDef(
        key="tech",
        kind="function",
        label="技術スタック・環境の制約",
        question="使いたい(使わなければならない)言語・フレームワーク・置き場所はありますか",
        required=_ALL,
        done_when=(
            "サーバー側(または全体)の言語・フレームワークと、置き場所(クラウド・VPS・"
            "社内サーバーなど)が分かる。AI を使うシステムなら、使う AI(LLM)も。"
            "DB やライブラリの名前だけでは決まっていない"
        ),
        confirm_in_chat=True,
    ),
    PointDef(
        key="schedule",
        kind="function",
        label="体制・期間",
        question="何人で、いつまでに作りますか",
        required=_NONE,
        done_when="作る人数と期限が分かる",
    ),
    PointDef(
        key="availability",
        kind="nonfunctional",
        label="可用性",
        question="夜中や休日に止まっていたら、どのくらいで困りますか",
        required=_ALL,
        done_when="止まったとき、どのくらいで困るかが分かる",
    ),
    PointDef(
        key="backup",
        kind="nonfunctional",
        label="可用性(データの保全)",
        question="入れたデータが消えてしまったら、どのくらい前の状態まで戻せればよいですか",
        required=_ALL,
        done_when=(
            "データを失ったときにどこまで戻せればよいか(バックアップの要否・頻度・残す期間の"
            "どれか)が分かる"
        ),
        category="可用性",
    ),
    PointDef(
        key="performance",
        kind="nonfunctional",
        label="性能・拡張性",
        question="同時に何人くらいが使いますか。待てるのは何秒までですか",
        required=_ALL,
        done_when="同時に使う人数か、待てる時間が分かる",
    ),
    PointDef(
        key="operability",
        kind="nonfunctional",
        label="運用・保守性",
        question="止まったことに、誰がどうやって気づけばよいですか",
        required=_INTERNAL_UP,
        done_when="止まったこと・エラーに、誰がどう気づくかが分かる",
    ),
    PointDef(
        key="data_retention",
        kind="nonfunctional",
        label="運用・保守性(データの保持・削除)",
        question="作ったデータやアカウントを、利用者が消せる必要はありますか。いつまで残しますか",
        required=_ALL,
        done_when=(
            "データ・アカウントを消せる必要があるか(あるなら、いつ・誰が消すか)、"
            "または残す期間が分かる"
        ),
        category="運用・保守性",
    ),
    PointDef(
        key="migration",
        kind="nonfunctional",
        label="移行性",
        question="今使っている仕組みやデータから、移すものはありますか",
        required=_INTERNAL_UP,
        done_when="移すデータ・仕組み(無いなら、無いこと)が分かる",
    ),
    PointDef(
        key="security",
        kind="nonfunctional",
        label="セキュリティ",
        question="入れたデータを、誰に見られたら困りますか。ログインはどうしますか",
        required=_ALL,
        done_when="見られて困る相手と、ログインの要否(要るなら、その方法)が分かる",
    ),
    PointDef(
        key="logging",
        kind="nonfunctional",
        label="セキュリティ(ログ)",
        question="動きの記録(ログ)に、入力した内容を残してよいですか",
        required=_INTERNAL_UP,
        done_when="記録(ログ)に残してよいもの・残してはいけないものが分かる",
        category="セキュリティ",
    ),
    PointDef(
        key="abuse",
        kind="nonfunctional",
        label="セキュリティ(不正利用)",
        question="なりすましや、機械的な大量の登録・利用を防ぐ必要はありますか",
        required=_INTERNAL_UP,
        done_when=(
            "防ぎたい不正な使い方(総当たり・大量の登録や利用など)と、防ぐ必要があるかが分かる"
        ),
        category="セキュリティ",
    ),
    PointDef(
        key="environment",
        kind="nonfunctional",
        label="システム環境・エコロジー",
        question="どの端末・ブラウザで使いますか",
        required=_INTERNAL_UP,
        done_when=(
            "利用者が使う端末・ブラウザが分かる。サーバーや開発の技術(フレームワークなど)は"
            "ここに含めない(技術スタックの観点)"
        ),
    ),
)

_DEFS_BY_KEY: dict[str, PointDef] = {d.key: d for d in POINT_DEFS}


@dataclass(frozen=True)
class PointState:
    """観点1つの状態。`evidence`は根拠の発言(`chat_histories.id`の文字列)。`reopened_after`は、
    設計レビューの指摘で未決に戻したときの聞き直しの発言(`chat_histories.id`の文字列)。未決に
    戻した観点は、それより後の利用者の発言を根拠にしないと決まったことにしない(前の答えで、
    すぐに決まったことに戻らないように)。"""

    key: PointKey
    status: PointStatus
    summary: str
    evidence: tuple[str, ...] = ()
    reopened_after: str | None = None


@dataclass(frozen=True)
class JudgedPoint:
    """判定の LLM が返した観点1つ。`evidence`は発言の id に直したもの(結べなかった番号は除く)。"""

    key: str
    status: str
    summary: str
    evidence: tuple[str, ...]


def point_def(key: str) -> PointDef:
    return _DEFS_BY_KEY[key]


def is_point_key(key: str) -> bool:
    return key in _DEFS_BY_KEY


def initial_states() -> dict[str, PointState]:
    """すべて未決の表(まだ判定していないプロジェクト)。"""
    return {d.key: PointState(d.key, "open", "") for d in POINT_DEFS}


def as_scale(value: str | None) -> Scale | None:
    """`projects.hearing_scale`の値を規模にする(未決・知らない値は None)。"""
    return value if value in get_args(Scale) else None  # type: ignore[return-value]


def effective_scale(scale: Scale | None) -> Scale:
    """必須を数える規模。決まるまでは「個人・小規模」の列で数える。"""
    return scale or DEFAULT_SCALE


def is_required(key: str, scale: Scale | None) -> bool:
    return effective_scale(scale) in point_def(key).required


def open_required(states: Mapping[str, PointState], scale: Scale | None) -> list[PointDef]:
    """必須なのに未決の観点(定義の順)。表に無い観点は未決とみなす。"""
    return [
        d
        for d in POINT_DEFS
        if is_required(d.key, scale)
        and (states[d.key].status if d.key in states else "open") == "open"
    ]


def open_optional(states: Mapping[str, PointState], scale: Scale | None) -> list[PointDef]:
    """任意で未決の観点(定義の順)。必須がそろった後に、利用者が続けると選んだときに聞く。"""
    return [
        d
        for d in POINT_DEFS
        if not is_required(d.key, scale)
        and (states[d.key].status if d.key in states else "open") == "open"
    ]


def is_sufficient(states: Mapping[str, PointState], scale: Scale | None) -> bool:
    """ヒアリングの完了: 必須の観点がすべて未決以外(対象外でもよい)。"""
    return not open_required(states, scale)


def questions_for_reply(
    states: Mapping[str, PointState], scale: Scale | None, *, limit: int = 3
) -> list[str]:
    """返信の LLM に渡す「まだ確認できていない観点」。未決の必須を定義の順に最大`limit`件。"""
    return describe_points(open_required(states, scale)[:limit])


def describe_points(defs: Iterable[PointDef]) -> list[str]:
    """観点を「名前(例: 場面で聞く問い)」の形にする(返信の LLM に渡す)。"""
    return [f"{d.label}(例: {d.question})" for d in defs]


def to_state(
    key: str, status: str, summary: str, evidence: Iterable[str] = ()
) -> PointState | None:
    """観点のキーと状態を確かめて PointState にする。知らないキー・状態なら None。"""
    if not is_point_key(key) or status not in get_args(PointStatus):
        return None
    point_status: PointStatus = status  # type: ignore[assignment]
    return PointState(point_def(key).key, point_status, summary, tuple(evidence))


def merge_judgement(
    previous: Mapping[str, PointState],
    judged: Iterable[JudgedPoint],
    *,
    user_messages: Collection[str],
    after_reopen: Mapping[str, Collection[str]] | None = None,
) -> dict[str, PointState]:
    """判定の LLM の結果を、前の表に取り込む。`user_messages`は利用者の発言(sender='user')の id、
    `after_reopen`は未決に戻した観点ごとの、聞き直しより後の利用者の発言の id。

    - 知らない観点のキー・状態は捨てる。返らなかった観点は前の状態のまま。
    - 未決以外(決定・採用・対象外)は、根拠の発言が1つ以上あるときだけ取り込む。根拠が無ければ
      前の状態のまま(AI の推測で決まったことにしない)。
    - 「採用」と、チャットで確かめる観点(`confirm_in_chat`)の未決以外は、根拠に利用者の発言が
      あるときだけ取り込む。初期入力・添付は AI の提案に同意できないので、それだけを根拠にした
      採用は推測と同じ。規模・技術スタックは、初期入力に一部が書いてあっても利用者に確かめる。
    - 未決は根拠が無くても取り込む(利用者が決めたことを取り消した場合)。
    - 設計レビューの指摘で未決に戻した観点(`reopened_after`)は、聞き直しより後の利用者の発言が
      根拠にあるときだけ未決以外にする。未決のままなら、戻した印を引き継ぐ。
    """
    after_reopen = after_reopen or {}
    merged: dict[str, PointState] = initial_states()
    for key, state in previous.items():
        if is_point_key(key):
            merged[state.key] = state
    for item in judged:
        state = to_state(item.key, item.status, item.summary.strip(), item.evidence)
        if state is None or (state.status != "open" and not state.evidence):
            continue
        needs_user = state.status == "adopted" or (
            point_def(state.key).confirm_in_chat and state.status != "open"
        )
        if needs_user and not any(e in user_messages for e in state.evidence):
            continue
        reopened = merged[state.key].reopened_after
        if reopened is not None:
            if state.status == "open":
                state = replace(state, reopened_after=reopened)
            elif not any(e in after_reopen.get(state.key, ()) for e in state.evidence):
                continue
        merged[state.key] = state
    return merged


def build_summary(states: Mapping[str, PointState], scale: Scale | None) -> str:
    """十分になったときのまとめ。決まった観点を「観点: 決めたこと」で定義の順に並べる
    (LLM は呼ばない)。"""
    lines = [f"規模: {SCALE_LABEL[effective_scale(scale)]}"]
    for d in POINT_DEFS:
        state = states.get(d.key)
        if state is None or state.status == "open" or d.key == "scale":
            continue
        text = state.summary or STATUS_LABEL[state.status]
        if state.status == "not_applicable" and state.summary:
            text = f"対象外({state.summary})"
        lines.append(f"{d.label}: {text}")
    return "\n".join(f"- {line}" for line in lines)


def missing_labels(states: Mapping[str, PointState], scale: Scale | None) -> list[str]:
    """未決の必須の観点の名前(完了判定の`missing_points`)。"""
    return [d.label for d in open_required(states, scale)]


def scale_after(
    states: Mapping[str, PointState], previous: Scale | None, judged: Scale | None
) -> Scale | None:
    """判定の後の規模。規模の観点が未決なら None、決まっていれば判定の値(無ければ前の値)。"""
    state = states.get("scale")
    if state is None or state.status in ("open", "not_applicable"):
        return None
    return judged or previous


def number_messages(senders: Sequence[tuple[str, str]]) -> list[tuple[str, int, str]]:
    """利用者側の発言(user・intake・attachment)に番号と表示名を付ける。

    `senders`は発生順の(発言の id, sender)。返り値は(id, 番号, 表示名)。番号は 1 から通しで、
    LLM に見せる。表示名は画面の根拠の欄に出す(初期入力 / 添付資料 / 発言N。N は利用者の
    発言だけを数える)。
    """
    numbered: list[tuple[str, int, str]] = []
    user_count = 0
    for message_id, sender in senders:
        if sender == "user":
            user_count += 1
            label = f"発言{user_count}"
        elif sender == "intake":
            label = "初期入力"
        elif sender == "attachment":
            label = "添付資料"
        else:
            continue
        numbered.append((message_id, len(numbered) + 1, label))
    return numbered
