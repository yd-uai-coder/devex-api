import uuid

from app.uml.domain import ComponentSemanticModel, DfdSemanticModel, ErSemanticModel
from app.uml.validation.base import ValidationIssue, ValidationResult
from app.uml.validation.dfd_rules import validate_dfd_rules
from app.uml.validation.structural import validate_structure


def validate_diagram(
    model: ComponentSemanticModel | ErSemanticModel | DfdSemanticModel,
    *,
    data_item_ids: set[uuid.UUID] | None = None,
) -> ValidationResult:
    """意味モデル1件を検証し、ValidationResultを返す(例外は投げない)。

    全notation共通で構造検証(app.uml.validation.structural)を行い、notationが'dfd'の場合のみ
    DFD固有規則(app.uml.validation.dfd_rules)も追加で行う。`data_item_ids`はDFD検証でのみ使う
    (プロジェクトのデータ辞書全件のID。component/erでは無視する)。
    """
    errors, warnings = validate_structure(model.elements, model.relations)

    if isinstance(model, DfdSemanticModel):
        dfd_errors, dfd_warnings = validate_dfd_rules(
            model.elements, model.relations, data_item_ids or set()
        )
        errors = [*errors, *dfd_errors]
        warnings = [*warnings, *dfd_warnings]

    return ValidationResult(errors=errors, warnings=warnings)


__all__ = ["ValidationIssue", "ValidationResult", "validate_diagram"]
