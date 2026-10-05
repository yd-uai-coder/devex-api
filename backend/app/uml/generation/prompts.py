"""UML図の生成のプロンプトに渡す、既存のデータ辞書の形。

詳細設計モードの段階2・3の下書き(app/detailed_design/data_flow_drafting.py・data_model_drafting.py)
が使う。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExistingDataItem:
    """プロンプトに渡す既存データ辞書1件(名前とフィールド名)。"""

    name: str
    field_names: list[str]
