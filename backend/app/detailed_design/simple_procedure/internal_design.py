"""簡易ドキュメントモードの内部設計書(と外部設計書の API 一覧)の解析(純粋関数)。

docs/internal_design.md 3.3節「5. 実装手順書」の簡易モード。作成方針 10章の参照先の対応:

- 処理の流れ → 内部設計書 3.3節「処理別データフロー」の`#### DF-<n>`(段階5の手順の代わり)。
  流れに出てくるテーブル(3.2節)を添える
- API の契約 → 外部設計書 2.6節と、内部設計書 3.3節の API エンドポイント一覧
- モジュール → 内部設計書 3.3節「モジュール一覧」(段階4の代わり)。簡易モードの一覧は層ごとに
  まとめた行(`app/routers/*.py`)なので、ファイルは層まで照合する(`module_layer`)
- エラー・ログ → 内部設計書 3.4節(07章の代わり)
- 技術スタック → 内部設計書 3.1節
- 関数の契約(段階6)・シーケンス図は簡易モードに無い(手順書の AI が「未定義」に挙げる)

書式は内部設計書のプロンプト(app/services/doc_generator_service.py)で決まった見出しと表。
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from app.detailed_design.api_list import (
    ApiEndpoint,
    extract_api_endpoints,
    is_separator_row,
    parse_trigger,
    table_cells,
)
from app.detailed_design.structure import ModuleRow, path_variants
from app.uml.generation.sections import extract_section

ARCHITECTURE_SECTION = "3.1"
DATA_MODEL_SECTION = "3.2"
MODULE_SECTION = "3.3"
ERROR_POLICY_SECTION = "3.4"

_TABLE_HEADING = re.compile(r"^###\s+テーブル\s*[:：]\s*(.+?)\s*$")
# テーブルの見出しの名前の後ろの説明(`reservations(予約)`・`reservations (予約)`)
_TABLE_NOTE = re.compile(r"\s*[(（].*$")
_MODULE_LIST_HEADING = re.compile(r"^###\s+(?:\d+\.\s*)?モジュール一覧")
_DATAFLOW_HEADING = re.compile(r"^####\s+(DF-\d+)\s*[:：]\s*(.*?)\s*$")
_EMPTY_CELLS = {"", "—", "-", "なし"}


@dataclass(frozen=True)
class DataFlow:
    """処理別データフロー1つ。`markdown`は見出しの下の本文(流れの表とデータ項目)。
    `tables`は本文に名前の出てくる 3.2節のテーブル(3.2節の順。表の先が
    「データベース (`trends`, `trend_sources` テーブル)」のように書かれても拾う)。"""

    id: str
    title: str
    markdown: str
    tables: tuple[str, ...] = ()

    @property
    def trigger(self) -> tuple[str, str] | None:
        """見出しが API(`POST /api/v1/x`)なら(メソッド, パス)。バッチなら None。"""
        return parse_trigger(self.title)


@dataclass(frozen=True)
class SimpleDesignBook:
    """簡易モードの手順書が参照する設計(内部設計書・外部設計書から読んだもの)。

    - `modules`: パス → モジュール一覧の行。`has_module_list`はモジュール一覧の節があるか
      (モジュール一覧を書かせる前に生成した内部設計書には無い)
    - `dataflows`: DF の ID → 処理別データフロー
    - `apis`・`external_apis`: 照合のキー → 内部設計書 3.3・外部設計書 2.6 の API
    - `tables`: テーブル名 → 3.2節のテーブルの md。`data_model`は 3.2節の本文(DF を持たない
      単位に、データモデル全体として添える)
    - `architecture`・`error_policy`: 3.1節・3.4節の本文
    """

    modules: Mapping[str, ModuleRow] = field(default_factory=dict)
    has_module_list: bool = False
    dataflows: Mapping[str, DataFlow] = field(default_factory=dict)
    apis: Mapping[str, ApiEndpoint] = field(default_factory=dict)
    external_apis: Mapping[str, ApiEndpoint] = field(default_factory=dict)
    tables: Mapping[str, str] = field(default_factory=dict)
    data_model: str = ""
    architecture: str = ""
    error_policy: str = ""


def parse_internal_design(internal_design: str, external_design: str = "") -> SimpleDesignBook:
    """内部設計書(と外部設計書の 2.6節)から、簡易モードの手順書が参照する設計を読む。"""
    module_section = extract_section(internal_design, MODULE_SECTION)
    module_lines = _subsection(module_section, _MODULE_LIST_HEADING)
    data_model = extract_section(internal_design, DATA_MODEL_SECTION)
    tables = _tables(data_model)
    return SimpleDesignBook(
        modules=_modules(module_lines or []),
        has_module_list=module_lines is not None,
        dataflows=_dataflows(module_section, tuple(tables)),
        apis={a.key: a for a in extract_api_endpoints(internal_design, MODULE_SECTION)},
        external_apis={a.key: a for a in extract_api_endpoints(external_design)},
        tables=tables,
        data_model=_body(data_model),
        architecture=_body(extract_section(internal_design, ARCHITECTURE_SECTION)),
        error_policy=_body(extract_section(internal_design, ERROR_POLICY_SECTION)),
    )


def _subsection(section: str, heading: re.Pattern[str]) -> list[str] | None:
    """`###`の小節の本文の行(見出しは含めない。次の`###`の直前まで)。無ければ None。"""
    lines = section.splitlines()
    for index, line in enumerate(lines):
        if heading.match(line.strip()):
            body: list[str] = []
            for rest in lines[index + 1 :]:
                if rest.startswith("### ") or rest.startswith("## "):
                    break
                body.append(rest)
            return body
    return None


def _modules(lines: list[str]) -> dict[str, ModuleRow]:
    """モジュール一覧の表(パス/層/責務/主な依存先)を読む。同じパスは最初の行だけ。"""
    modules: dict[str, ModuleRow] = {}
    for row in _table_rows(lines):
        path = row.get("パス", "").strip("`").strip()
        if not path or path in modules:
            continue
        depends = re.split(r"[,、，]", row.get("主な依存先", ""))
        modules[path] = ModuleRow(
            path=path,
            layer=row.get("層", ""),
            responsibility=row.get("責務", ""),
            depends_on=[d.strip().strip("`") for d in depends if d.strip() not in _EMPTY_CELLS],
        )
    return modules


def module_layer(path: str, book: SimpleDesignBook) -> ModuleRow | None:
    """ファイルのパスが属する、モジュール一覧の行(層)。無ければ None。

    簡易モードのモジュール一覧は層ごとにまとめた行(`app/routers/*.py`)が多く、WBS のファイル
    (`backend/app/routers/trends.py`)と完全一致しない。次の順で、層まで照合する。
    1. 一覧の行と完全一致する。
    2. 行のパターン(`*`・`{a,b}`)が、パスの末尾の区切りに一致する(`backend/`のような
       前置きは見ない)。
    3. 行のディレクトリが、パスのディレクトリの末尾と一致する(同じ層のディレクトリにある)。
    """
    text = path.strip().strip("`")
    exact = book.modules.get(text)
    if exact is not None:
        return exact
    targets = path_variants(text)
    if not targets:
        return None
    target = targets[0]
    rows = list(book.modules.values())
    for row in rows:
        if any(_suffix_matches(target, pattern) for pattern in path_variants(row.path)):
            return row
    for row in rows:
        directories = [pattern[:-1] for pattern in path_variants(row.path) if len(pattern) > 1]
        if any(_suffix_matches(target[:-1], directory) for directory in directories):
            return row
    return None


def _suffix_matches(target: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    """`pattern`の区切りの列が、`target`の末尾に(区切りごとに`fnmatch`で)一致するか。"""
    if not pattern or len(pattern) > len(target):
        return False
    tail = target[len(target) - len(pattern) :]
    return all(fnmatchcase(have, want) for have, want in zip(tail, pattern, strict=True))


def _dataflows(section: str, table_names: tuple[str, ...]) -> dict[str, DataFlow]:
    """`#### DF-<n>: <見出し>`ごとに、次の見出しまでの本文を読む。同じ ID は最初だけ。"""
    flows: dict[str, DataFlow] = {}
    current: tuple[str, str] | None = None
    body: list[str] = []

    def close() -> None:
        if current is not None and current[0] not in flows:
            markdown = "\n".join(body).strip()
            flows[current[0]] = DataFlow(
                id=current[0],
                title=current[1],
                markdown=markdown,
                tables=_mentioned(markdown, table_names),
            )

    for line in section.splitlines():
        match = _DATAFLOW_HEADING.match(line.strip())
        if match is not None:
            close()
            current, body = (match.group(1), match.group(2)), []
        elif line.startswith("#"):
            close()
            current, body = None, []
        elif current is not None:
            body.append(line)
    close()
    return flows


def _mentioned(markdown: str, table_names: tuple[str, ...]) -> tuple[str, ...]:
    """本文に識別子として名前の出てくるテーブル(3.2節の順)。`trends`は`trend_sources`の中では
    一致させない(前後が英数字・`_`でないところだけ)。"""
    return tuple(
        name
        for name in table_names
        if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", markdown)
    )


def _tables(section: str) -> dict[str, str]:
    """`### テーブル: <名前>`ごとに、次の見出しまでの本文(カラムの表)を読む。"""
    tables: dict[str, str] = {}
    name: str | None = None
    body: list[str] = []
    for line in [*section.splitlines(), "### "]:
        if line.startswith("#"):
            if name is not None and name not in tables:
                tables[name] = "\n".join(body).strip()
            match = _TABLE_HEADING.match(line.strip())
            name, body = (_table_name(match.group(1)), []) if match else (None, [])
        elif name is not None:
            body.append(line)
    return tables


def _table_name(heading: str) -> str:
    """テーブルの見出しの名前(`` ` ``と、後ろの説明を除く)。"""
    return _TABLE_NOTE.sub("", heading.replace("`", "")).strip()


def _table_rows(lines: list[str]) -> list[dict[str, str]]:
    """Markdown の表を、見出しの名前 → セルの辞書の列にする(表が複数あれば続けて読む)。"""
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in lines:
        cells = table_cells(line)
        if cells is None:
            header = None
            continue
        if header is None:
            header = cells
            continue
        if is_separator_row(cells):
            continue
        rows.append(dict(zip(header, cells, strict=False)))
    return rows


def _body(section: str) -> str:
    """節の見出し行を除いた本文。"""
    return "\n".join(section.splitlines()[1:]).strip()
