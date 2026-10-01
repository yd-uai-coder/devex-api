"""UML図のAI生成(M1、Phase 10)。内部設計書の節抽出・LLM出力スキーマ・プロンプト組み立て・
ドメインモデルへの変換・失敗理由の分類を、いずれも純粋関数/純粋なデータとして持つ。
LLM呼び出しとDB操作はサービス層(app/services/uml_generation_service.py)が担う。"""

from app.uml.generation.failures import (
    SKIPPED_MESSAGE,
    STALE_MESSAGE,
    GenerationFailure,
    ReasonCode,
    classify_failure,
    unwrap_structured_result,
)
from app.uml.generation.mapper import (
    required_data_items,
    to_component,
    to_dfd,
    to_er,
    to_semantic_model,
)
from app.uml.generation.prompts import (
    ExistingDataItem,
    build_generation_messages,
    build_source_text,
)
from app.uml.generation.schemas import (
    GENERATION_SCHEMAS,
    ComponentGenerationOutput,
    DfdGenerationOutput,
    ErGenerationOutput,
    GenerationOutput,
)
from app.uml.generation.sections import (
    DFD_SECTION_TITLE,
    DfdSubject,
    extract_dfd_subjects,
    extract_er_table_blocks,
    extract_er_tables,
    extract_section,
    remove_subsection,
)

__all__ = [
    "DFD_SECTION_TITLE",
    "GENERATION_SCHEMAS",
    "SKIPPED_MESSAGE",
    "STALE_MESSAGE",
    "ComponentGenerationOutput",
    "DfdGenerationOutput",
    "DfdSubject",
    "ErGenerationOutput",
    "ExistingDataItem",
    "GenerationFailure",
    "GenerationOutput",
    "ReasonCode",
    "build_generation_messages",
    "build_source_text",
    "classify_failure",
    "extract_dfd_subjects",
    "extract_er_table_blocks",
    "extract_er_tables",
    "extract_section",
    "remove_subsection",
    "required_data_items",
    "to_component",
    "to_dfd",
    "to_er",
    "to_semantic_model",
    "unwrap_structured_result",
]
