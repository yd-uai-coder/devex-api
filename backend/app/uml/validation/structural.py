from collections.abc import Sequence

from app.uml.domain.base import UmlElement, UmlRelation
from app.uml.validation.base import ValidationIssue

# 診断3(appendix/stage3-requirements-organization.md): ノード数上限の目安。
# 大規模図はPhase 9のレイアウトエンジンが`asyncio.to_thread`実行前提のO(n^2)〜O(n!)アルゴリズムを
# 使うため、超過時は警告にとどめる(エラーにして保存自体を止めない)。
MAX_ELEMENTS = 30


def validate_structure(
    elements: Sequence[UmlElement], relations: Sequence[UmlRelation]
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """全notation共通の構造検証(M4): ID重複・参照切れ・ノード数上限。
    「関係種別」「必須」「型」はPydanticの意味モデル(app/uml/domain)がparse時点で
    保証済みのため、ここでは扱わない(不正な型・欠落フィールドはそもそもSemanticModelAdapterの
    ValidationErrorとして弾かれ、この関数に到達する前に検出される)。「座標」はlayout_model
    (Phase 9)側の関心事であり、semantic_modelには含まれないためここでは扱わない。
    """
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    element_ids = [el.id for el in elements]
    errors.extend(_duplicate_id_issues(element_ids, label="要素"))

    relation_ids = [rel.id for rel in relations]
    errors.extend(_duplicate_id_issues(relation_ids, label="関係"))

    known_ids = set(element_ids)
    for rel in relations:
        if rel.source_id not in known_ids:
            errors.append(
                ValidationIssue(
                    code="DANGLING_REFERENCE",
                    message=f"関係{rel.id}のsource_id「{rel.source_id}」に対応する要素がありません",
                    element_id=rel.id,
                )
            )
        if rel.target_id not in known_ids:
            errors.append(
                ValidationIssue(
                    code="DANGLING_REFERENCE",
                    message=f"関係{rel.id}のtarget_id「{rel.target_id}」に対応する要素がありません",
                    element_id=rel.id,
                )
            )

    if len(elements) > MAX_ELEMENTS:
        warnings.append(
            ValidationIssue(
                code="TOO_MANY_ELEMENTS",
                message=f"要素数が上限目安({MAX_ELEMENTS})を超えています: {len(elements)}件",
            )
        )

    return errors, warnings


def _duplicate_id_issues(ids: list[str], *, label: str) -> list[ValidationIssue]:
    """同一idが2回以上出現した場合、2回目以降の出現ごとに1件のissueを積む。"""
    seen: set[str] = set()
    issues: list[ValidationIssue] = []
    for id_ in ids:
        if id_ in seen:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_ID", message=f"{label}idが重複しています: {id_}", element_id=id_
                )
            )
        seen.add(id_)
    return issues
