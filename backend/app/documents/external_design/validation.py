"""外部設計書の検証(純粋関数)。指摘は警告だけで、生成も保存も止めない。

要件ID は生成した時点の要件定義書の写し(`model.known_req_ids`・`must_fr_ids`)と照らす。写しが
空(要件定義書に model が無かった)なら、要件ID の照合はしない。
"""

from app.documents.external_design.model import BODY_METHODS, NO_BODY, ExternalDesignModel
from app.documents.issues import DocumentIssue


def row_counts(model: ExternalDesignModel) -> dict[str, int]:
    """画面・API・外部連携の行数。"""
    return {
        "screens": len(model.screens),
        "apis": len(model.apis),
        "integrations": len(model.integrations),
    }


def covered_by(model: ExternalDesignModel, req_id: str) -> tuple[list[str], list[str]]:
    """要件ID を持つ画面ID と API(`POST /path`)。付録と検証が使う。"""
    screens = [s.id for s in model.screens if req_id in s.req_ids]
    apis = [a.label for a in model.apis if req_id in a.req_ids]
    return screens, apis


def validate_external_design(model: ExternalDesignModel) -> list[DocumentIssue]:
    issues: list[DocumentIssue] = []
    uncovered = [r for r in model.must_fr_ids if covered_by(model, r) == ([], [])]
    if uncovered:
        issues.append(
            DocumentIssue(
                "major",
                "MUST_REQUIREMENT_UNCOVERED",
                "画面も API も無い Must の機能要件があります: " + "・".join(uncovered)
                + "(一覧に足すか、要件を見直してください)",
                tuple(uncovered),
            )
        )
    no_body = [a.label for a in model.apis if a.method in BODY_METHODS and not a.body]
    if no_body:
        issues.append(
            DocumentIssue(
                "major",
                "API_BODY_MISSING",
                "本文の欄が空の API があります: " + "・".join(no_body)
                + f"(本文の要らない API は「{NO_BODY}」と書いてください)",
                tuple(no_body),
            )
        )
    if model.known_req_ids:
        known = set(model.known_req_ids)
        referenced = [
            *(i for s in model.screens for i in s.req_ids),
            *(i for a in model.apis for i in a.req_ids),
            *(i for g in model.integrations for i in g.req_ids),
        ]
        unknown = list(dict.fromkeys(i for i in referenced if i not in known))
        if unknown:
            issues.append(
                DocumentIssue(
                    "minor",
                    "UNKNOWN_REQUIREMENT_ID",
                    "要件定義書に無い要件ID があります: " + "・".join(unknown),
                    tuple(unknown),
                )
            )
    unlinked = [s.id for s in model.screens if not s.req_ids]
    unlinked += [a.label for a in model.apis if not a.req_ids]
    if unlinked:
        issues.append(
            DocumentIssue(
                "minor",
                "UNLINKED_ROWS",
                "どの要件にも結ばれていない画面・API があります: " + "・".join(unlinked),
                tuple(unlinked),
            )
        )
    screen_ids = {s.id for s in model.screens}
    unknown_screens = list(
        dict.fromkeys(ref for a in model.apis for ref in a.screens if ref not in screen_ids)
    )
    if unknown_screens:
        issues.append(
            DocumentIssue(
                "minor",
                "UNKNOWN_SCREEN_REF",
                "画面一覧に無い画面ID を指す API があります: " + "・".join(unknown_screens),
                tuple(unknown_screens),
            )
        )
    return issues
