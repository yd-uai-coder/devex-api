"""UML図の生成の部品(詳細設計モードの段階の下書きが使う)。出力スキーマ・ドメインモデルへの変換・
失敗理由の分類・節の抽出を、いずれも純粋関数/純粋なデータとして持つ。
LLM呼び出しとDB操作はサービス層(app/services/design_stage_generation_service.py)が担う。"""

from app.uml.generation.failures import (
    GenerationFailure,
    ReasonCode,
    classify_failure,
    unwrap_structured_result,
)
from app.uml.generation.mapper import required_data_items, to_component, to_dfd
from app.uml.generation.prompts import ExistingDataItem
from app.uml.generation.schemas import ComponentGenerationOutput, DfdGenerationOutput
from app.uml.generation.sections import extract_section

__all__ = [
    "ComponentGenerationOutput",
    "DfdGenerationOutput",
    "ExistingDataItem",
    "GenerationFailure",
    "ReasonCode",
    "classify_failure",
    "extract_section",
    "required_data_items",
    "to_component",
    "to_dfd",
    "unwrap_structured_result",
]
