"""段階4 ソフトウェア構造の意味モデルと、モジュール一覧の組み立て(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」。

段階4は、構成図(層)とモジュール一覧の2つを持つ。

- 構成図: `uml_diagrams`(notation=component、subject='')の1枚が正本。箱はパッケージ単位の
  モジュールで、`layer`(層)が図のレーンになる。段階3の ER と同じく、`design_stages.model`には
  複製しない(SCR-007 のエディタで直した内容と食い違わないように)。
- モジュール一覧: ファイル単位の責務表(パス / 層 / 責務 / 主な依存先 / 関わる処理)。
  `design_stages.model`(段階4)に持つ。arc42 の Level 1(図)と Level 2(責務表)に当たる。

モジュールのパスは、段階5の「処理 × モジュール」の関与表の列の鍵になる(手順の呼び出し先は
この一覧のパスで書く)。そのためパスの重複は検証のエラーにする。

「関わる処理」は AI が下書きし、人が直す(CRUD 図から決定的には作らない。テーブルとモジュールの
対応は名前の推測になり、外れると固定部分が壊れるため)。`all_functions`は、
アプリの組み立て・DI・例外の変換のように全処理が通る横断のモジュールの印で、文書では「全処理」と
書く。
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.detailed_design.function_list import FunctionListModel
from app.uml.domain.component import ComponentSemanticModel

# ソフトウェア構造の段階の番号(構成図を直したときに差し戻す段階。app/services から参照する)
STRUCTURE_STAGE = 4

# 段階4の構成図を`uml_diagrams`で識別するキー(subject)。全体1枚なので空文字列
STRUCTURE_SUBJECT = ""


class ModuleRow(BaseModel):
    """モジュール一覧の1行。

    - `path`: ファイルのパス(`app/services/foo.py`)。似たファイルは`{a,b}.py`・`*`でまとめてよい。
    - `layer`: 層。構成図のレーン(要素の`layer`)の名前にそろえる。
    - `depends_on`: 主な依存先。一覧の他のモジュールはそのパスで、外部のライブラリは名前で書く。
    - `functions`: 関わる処理の処理ID(機能一覧の順)。
    - `all_functions`: 全処理が通る横断のモジュールの印。
    """

    path: str
    layer: str = ""
    responsibility: str = ""
    depends_on: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    all_functions: bool = False


class ModuleListModel(BaseModel):
    """段階4の意味モデル。"""

    modules: list[ModuleRow] = Field(default_factory=list)


@dataclass(frozen=True)
class ModuleDraft:
    """AIの下書きのモジュール一覧の1行。"""

    path: str
    layer: str
    responsibility: str
    depends_on: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    all_functions: bool = False


def component_layers(semantic_model: Mapping[str, Any] | None) -> list[str]:
    """構成図の層(要素の`layer`)を、図の要素の並びの初出順に返す(空・未設定は除く)。"""
    model = ComponentSemanticModel.model_validate(semantic_model or {})
    layers: list[str] = []
    for element in model.elements:
        layer = (element.layer or "").strip()
        if layer and layer not in layers:
            layers.append(layer)
    return layers


_BRACES = re.compile(r"\{([^{}]*)\}")


def path_variants(path: str) -> list[tuple[str, ...]]:
    """パスを「/」で区切った単位の列にする(依存先の照合に使う)。

    - 前後の空白と、先頭・末尾の「/」は除く。空の単位は捨てる。
    - 最後の単位の拡張子(最後の「.」以降。`.py`・`.ts` など)は除く(`app/main.py` と `app/main` を
      同じものとして扱う)。
    - `{a,b}` は展開する(`app/{a,b}.py` は2つの列になる)。
    """
    text = path.strip().strip("/")
    match = _BRACES.search(text)
    if match is not None:
        variants: list[tuple[str, ...]] = []
        for option in match.group(1).split(","):
            expanded = text[: match.start()] + option.strip() + text[match.end() :]
            variants.extend(path_variants(expanded))
        return list(dict.fromkeys(variants))
    units = [unit.strip() for unit in text.split("/") if unit.strip()]
    if units and "." in units[-1].lstrip("."):
        units[-1] = units[-1].rsplit(".", 1)[0]
    return [tuple(units)] if units else []


def module_ref_matches(ref: str, path: str) -> bool:
    """依存先`ref`が、モジュール一覧の`path`を指しているか(区切り単位の部分一致)。

    `ref`の単位の列が、`path`の単位の列のどこかに連続して現れれば一致とする。ディレクトリ
    (`app/services`)や、先頭を省いた短い書き方(`services/auth`)も一致する。文字列の途中では
    一致させない(`app/ser` は `app/services/…` に、`app/models` は `app/models_old.py` に
    一致しない。打ち間違いや似た名前を見落とさないため)。`path`側の`*`(`*.py`を含む)は、
    どの単位とも一致する(`app/models/*.py` のようにまとめた行)。
    """
    refs = path_variants(ref)
    for target in path_variants(path):
        for wanted in refs:
            size = len(wanted)
            for start in range(len(target) - size + 1):
                window = target[start : start + size]
                if all(have in ("*", want) for have, want in zip(window, wanted, strict=True)):
                    return True
    return False


def merge_modules(
    drafts: Sequence[ModuleDraft], function_list: FunctionListModel
) -> ModuleListModel:
    """AIの下書きから、モジュール一覧を組み立てる(再生成では前の版を使わず置き換える)。

    - パスが空の行と、同じパスの2つ目以降の行は捨てる(パスは関与表の列の鍵のため)。
    - 機能一覧に無い処理IDは捨て、残りを機能一覧の順に並べる(重複も除く)。
    - 依存先は前後の空白を除き、空と重複を捨てる。
    """
    order = {row.id: index for index, row in enumerate(function_list.functions)}
    rows: list[ModuleRow] = []
    seen: set[str] = set()
    for draft in drafts:
        path = draft.path.strip()
        if not path or path in seen:
            continue
        seen.add(path)
        known = {f.strip() for f in draft.functions if f.strip() in order}
        functions = sorted(known, key=lambda function_id: order[function_id])
        rows.append(
            ModuleRow(
                path=path,
                layer=draft.layer.strip(),
                responsibility=draft.responsibility.strip(),
                depends_on=list(dict.fromkeys(d.strip() for d in draft.depends_on if d.strip())),
                functions=functions,
                all_functions=draft.all_functions,
            )
        )
    return ModuleListModel(modules=rows)
