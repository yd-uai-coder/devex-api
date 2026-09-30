"""UML図のレビュー状態(`uml_diagrams.status`)の遷移規則(M7)。

状態は `draft → reviewing → approved → exported` と進む。遷移のきっかけはAPIの操作で、
この表をサービス層に散らさず、純粋関数として1か所に置く(DBにもHTTPにも依存しない)。

| 操作 | 遷移 |
|---|---|
| 保存(PUT。座標だけの保存を含む)・自動レイアウト | どの状態からでも → reviewing |
| 承認(POST approve) | draft / reviewing → approved(それ以外は拒否) |
| 出力(GET export) | approved / exported → exported(それ以外は拒否) |
| AIによる再生成 | どの状態からでも → draft(`uml_generation_service.py`) |

- 承認を`draft`からも許すのは、AIの出力を手直しせずにそのまま承認するケースがあるため。
- 承認済みの図を保存すると`reviewing`へ戻す(承認は「その内容」に対するものなので、
  内容・配置が変わったら承認をやり直す)。
- 状態が変わっても`version`は増やさない。`version`は内容の楽観ロック専用である。
"""

from typing import Literal, get_args

DiagramStatus = Literal["draft", "reviewing", "approved", "exported"]

DIAGRAM_STATUSES: tuple[DiagramStatus, ...] = get_args(DiagramStatus)

_APPROVABLE: frozenset[DiagramStatus] = frozenset({"draft", "reviewing"})
_EXPORTABLE: frozenset[DiagramStatus] = frozenset({"approved", "exported"})


def parse_status(value: str) -> DiagramStatus:
    """DBの文字列(`uml_diagrams.status`はVARCHAR)を`DiagramStatus`へ変換する。
    未知の値は、データが壊れているとみなして`ValueError`にする。"""
    for status in DIAGRAM_STATUSES:
        if status == value:
            return status
    raise ValueError(f"unknown diagram status: {value!r}")


# 保存・自動レイアウトの後の状態(どの状態から編集してもレビュー中になる)
STATUS_AFTER_EDIT: DiagramStatus = "reviewing"

# 承認の後の状態
STATUS_AFTER_APPROVE: DiagramStatus = "approved"

# 出力の後の状態
STATUS_AFTER_EXPORT: DiagramStatus = "exported"


def can_approve(current: DiagramStatus) -> bool:
    """承認できる状態か(下書き・レビュー中)。"""
    return current in _APPROVABLE


def can_export(current: DiagramStatus) -> bool:
    """出力できる状態か(承認済み・出力済み)。"""
    return current in _EXPORTABLE
