"""必要なテストの一覧(純粋関数)。生成の入力に「必要なテスト」の表として渡し、AI が埋める。

どのテストを作るかを AI に任せると、安いモデルではテストの数が縮む(Phase 51 の測定)。そこで、
要件定義書・外部設計書の model から、技法ごとに作るべきテストをコードが決める:

| 観点 | 技法 | 元 |
|---|---|---|
| 正常 | 同値分割(有効なクラスの代表) | API ごとに1件 |
| 異常 | エラーの一覧の網羅 | API の「エラー」欄の HTTP ステータスごと(欄に無ければ1件) |
| 境界 | 境界値分析 | 本文を持つ API(`BODY_METHODS`で、本文が「なし」でない) |
| セキュリティ | 認可 | パスに`{…}`(ほかの利用者のリソースを指せる)を持つ API |
| 非機能 | ― | 非機能要件ごとに総合テスト1件 |
| 正常 | 状態遷移(主な経路) | Must の画面を通す流れの総合テスト1件 |
| 正常 | ― | API にも画面にも結ばれていない Must の機能要件ごとに総合テスト1件 |

一覧は生成の誘導だけに使う。決定的な検証(`validation.py`)は一覧と照らさない。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.documents.external_design.model import BODY_METHODS, NO_BODY, ExternalDesignModel
from app.documents.requirements.model import RequirementsModel
from app.documents.test_spec.model import CaseLevel, Viewpoint

# エラー欄の HTTP ステータス(4xx・5xx)
_STATUS = re.compile(r"(?<!\d)([45]\d\d)(?!\d)")


@dataclass(frozen=True)
class Obligation:
    """必要なテスト1件。`target`は API(`POST /api/v1/x`)か総合テストの対象。"""

    level: CaseLevel
    target: str
    viewpoint: Viewpoint
    reason: str
    req_ids: tuple[str, ...] = ()
    screens: tuple[str, ...] = ()


def error_statuses(errors: str) -> list[str]:
    """API の「エラー」欄から HTTP ステータス(4xx・5xx)を、出てきた順・重複なしで取り出す。"""
    return list(dict.fromkeys(_STATUS.findall(errors)))


def _api_obligations(external: ExternalDesignModel) -> list[Obligation]:
    result: list[Obligation] = []
    for api in external.apis:
        checks: list[tuple[Viewpoint, str]] = [("正常", "正しい入力で成功する")]
        statuses = error_statuses(api.errors)
        for status in statuses:
            checks.append(("異常", f"エラー欄の {status} を起こす"))
        if not statuses:
            checks.append(("異常", "エラー欄に HTTP ステータスが無い。失敗の扱いを確かめる"))
        if api.method.upper() in BODY_METHODS and api.body.strip() != NO_BODY:
            checks.append(("境界", "本文の項目の下限・上限・空"))
        if "{" in api.path:
            checks.append(("セキュリティ", "ほかの利用者のリソースを指す"))
        reqs, screens = tuple(api.req_ids), tuple(api.screens)
        result += [
            Obligation("integration", api.label, viewpoint, reason, reqs, screens)
            for viewpoint, reason in checks
        ]
    return result


def _system_obligations(
    requirements: RequirementsModel | None, external: ExternalDesignModel
) -> list[Obligation]:
    result: list[Obligation] = []
    must_screens = [s for s in external.screens if s.priority == "Must"]
    if must_screens:
        result.append(
            Obligation(
                "system",
                " → ".join(s.id for s in must_screens),
                "正常",
                "Must の画面を通す主な流れ",
                tuple(dict.fromkeys(r for s in must_screens for r in s.req_ids)),
                tuple(s.id for s in must_screens),
            )
        )
    if requirements is None:
        return result
    for row in requirements.rows:
        if row.kind == "NFR":
            result.append(
                Obligation("system", row.category or "非機能", "非機能", row.text, (row.id,))
            )
    linked = {r for a in external.apis for r in a.req_ids} | {
        r for s in external.screens for r in s.req_ids
    }
    for row in requirements.rows:
        if row.kind == "FR" and row.priority == "Must" and row.id not in linked:
            reason = f"API・画面に無い Must の要件: {row.text}"
            result.append(Obligation("system", row.id, "正常", reason, (row.id,)))
    return result


def required_tests(
    requirements: RequirementsModel | None, external: ExternalDesignModel
) -> list[Obligation]:
    """必要なテストの一覧(結合 → 総合の順)。"""
    return [*_api_obligations(external), *_system_obligations(requirements, external)]


def obligation_rows(obligations: Sequence[Obligation]) -> list[str]:
    """生成の入力に渡す表の行(`種類|対象|観点|理由|要件|画面`)。"""
    labels = {"integration": "結合", "system": "総合"}
    return [
        "|".join(
            (
                labels[o.level],
                o.target,
                o.viewpoint,
                o.reason,
                "・".join(o.req_ids) or "—",
                "・".join(o.screens) or "—",
            )
        )
        for o in obligations
    ]
