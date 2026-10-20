"""外したモデルの設定を後継へ移すデータ移行の、移す先の表のテスト。

SUT: RETIRED(alembic/versions/be106e6426be_retire_gemini_31_flash_lite.py)と
     app/ai/llm/catalog.py の find_model
ドライバ: 各テスト関数(移行ファイルを importlib で読み込み、表を直接読む)
スタブ不要 ── 表と find_model は純粋(副作用なし)で、DB を呼ばないため(行の書き換えは upgrade の
責務で、ここでは扱わない)。移行ファイル自身は app を import しないので、表とカタログの食い違いを
ここで確かめる。
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from app.ai.llm.catalog import Provider, find_model

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_PATH = _VERSIONS / "be106e6426be_retire_gemini_31_flash_lite.py"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("retire_gemini_31_flash_lite", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retired_models_left_the_catalog_and_successors_are_in_it() -> None:
    retired = _migration().RETIRED
    assert retired == {"gemini-3.1-flash-lite": "gemini-3.5-flash-lite"}
    for old, new in retired.items():
        assert find_model(Provider.GEMINI, old) is None
        assert find_model(Provider.GEMINI, new) is not None
