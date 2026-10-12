"""内部設計書の検証(純粋関数)。指摘は警告だけで、生成も保存も止めない。

外部設計書の API は生成した時点の写し(`model.external_apis`)と照らす。写しが空(外部設計書に
API が無かった)なら、その照合はしない。
"""

from app.detailed_design.api_list import trigger_key
from app.documents.internal_design.model import InternalDesignModel
from app.documents.issues import DocumentIssue


def row_counts(model: InternalDesignModel) -> dict[str, int]:
    """テーブル・API・モジュール・処理別データフローの行数。"""
    return {
        "tables": len(model.tables),
        "apis": len(model.apis),
        "modules": len(model.modules),
        "dataflows": len(model.dataflows),
    }


def validate_internal_design(model: InternalDesignModel) -> list[DocumentIssue]:
    issues: list[DocumentIssue] = []
    if not model.modules:
        issues.append(
            DocumentIssue(
                "critical",
                "NO_MODULE_LIST",
                "モジュール一覧がありません(実装手順書の単位のファイルの層が決まりません。"
                "内部設計書を再生成してください)",
            )
        )
    flow_keys = {trigger_key(f.trigger) for f in model.dataflows}
    uncovered = [a for a in model.external_apis if trigger_key(a) not in flow_keys]
    if uncovered:
        issues.append(
            DocumentIssue(
                "major",
                "API_WITHOUT_DATAFLOW",
                "処理別データフローの無い、外部設計書の API があります: " + "・".join(uncovered)
                + "(実装計画書の機能のタスクが処理を持てません)",
                tuple(uncovered),
            )
        )
    listed = {a.key for a in model.apis}
    unlisted = [
        f.id for f in model.dataflows if (key := trigger_key(f.trigger)) and key not in listed
    ]
    if unlisted:
        issues.append(
            DocumentIssue(
                "minor",
                "DATAFLOW_API_UNLISTED",
                "API エンドポイント一覧に無い API の処理別データフローがあります: "
                + "・".join(unlisted),
                tuple(unlisted),
            )
        )
    empty = [f.id for f in model.dataflows if not f.flows]
    if empty:
        issues.append(
            DocumentIssue(
                "minor",
                "DATAFLOW_EMPTY",
                "流れの行が無い処理別データフローがあります: " + "・".join(empty),
                tuple(empty),
            )
        )
    return issues
