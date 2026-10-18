"""レビューに既知として渡す、決定的な検証の指摘(純粋関数)。

文書の保存した model に、プレビューと同じ検証(app/documents の validate_*)を流す。
"""

from collections.abc import Callable, Mapping

from pydantic import BaseModel, ValidationError

from app.documents.external_design import ExternalDesignModel, validate_external_design
from app.documents.implementation_plan import (
    ImplementationPlanModel,
    validate_implementation_plan,
)
from app.documents.internal_design import InternalDesignModel, validate_internal_design
from app.documents.issues import DocumentIssue
from app.documents.requirements import RequirementsModel, validate_requirements

_VALIDATORS: dict[str, tuple[type[BaseModel], Callable[..., list[DocumentIssue]]]] = {
    "requirements": (RequirementsModel, validate_requirements),
    "external_design": (ExternalDesignModel, validate_external_design),
    "internal_design": (InternalDesignModel, validate_internal_design),
    "implementation_plan": (ImplementationPlanModel, validate_implementation_plan),
}


def known_issues(models: Mapping[str, dict | None]) -> list[tuple[str, DocumentIssue]]:
    """文書の種類 → 保存した model から、(文書の種類, 指摘)の組を渡した順に返す。model の
    無い版・壊れた model・知らない種類は飛ばす(md だけでレビューする)。"""
    issues: list[tuple[str, DocumentIssue]] = []
    for doc_type, raw in models.items():
        if raw is None or doc_type not in _VALIDATORS:
            continue
        model_class, validate = _VALIDATORS[doc_type]
        try:
            model = model_class.model_validate(raw)
        except ValidationError:
            continue
        issues += [(doc_type, issue) for issue in validate(model)]
    return issues
