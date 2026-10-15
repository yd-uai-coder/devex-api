"""段階8 実装可能性チェックのうち、作業単位が作るファイルの網羅と順序の判定(純粋関数)。

docs/internal_design.md 3.3節「5. 実装手順書」。

単位が作るファイルは、段階7(簡易モードは実装計画書の WBS)のモジュール・環境・設定のファイルと、
手順書の「作成・変更するファイル」を合わせたもの(`unit_files`)。ここから次の3つを決める。

- 依存先が後の単位で作られる(`late_dependencies`): モジュール一覧の行を最初に作る単位を、その行の
  作り手とみなす。行の依存先の作り手が、依存する単位より後ろで、その後に依存する側のファイルを
  変更する単位も無ければ、単位の順と依存が逆。
- テストのファイルが無い(`has_test_file`): テスト観点を書いた手順書に、テストのファイルが無い。
- テーブルを作る単位が無い(`table_coverage`): スキーマのファイル(モデル・マイグレーション)の
  パスと責務に、テーブルの名前が出てこない(名前の出ないスキーマのファイルは、全テーブルを作る
  まとめたファイルとみなす)。
- 異常系のテスト観点が無い(`error_test_gap`。簡易モードだけ): 単位の API のエラー欄に HTTP
  ステータスがあるのに、手順書のテスト観点にそのどれも出ず、異常系の言葉も無い。

モードの違い(ファイル → モジュール一覧の行の引き方)は、呼び出し側が`row_of`で渡す。
検証(validation.py)がこのモジュールを使うので、ここから validation は import しない。
"""

import re
from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass

from app.detailed_design.procedure_doc import PlanUnit, UnitFileKind, UnitProcedure
from app.detailed_design.structure import ModuleRow, module_ref_matches, path_variants

# ファイルのパス → そのファイルが属するモジュール一覧の行(無ければ None)
RowOf = Callable[[str], ModuleRow | None]


@dataclass(frozen=True)
class UnitFileRef:
    """単位が作るファイル1つ。`responsibility`は手順書の責務(無ければモジュール一覧の行の責務)。"""

    path: str
    kind: UnitFileKind
    responsibility: str = ""


def unit_files(unit: PlanUnit, doc: UnitProcedure | None, row_of: RowOf) -> list[UnitFileRef]:
    """単位が作るファイル。手順書のファイルを先に、段階7のモジュール・環境・設定のファイルのうち
    手順書に無いものを後に並べる(同じパスは1つにまとめる)。"""
    files: dict[str, UnitFileRef] = {}
    for file in doc.files if doc is not None else []:
        path = file.path.strip()
        if path and path not in files:
            files[path] = UnitFileRef(path, file.kind, file.responsibility.strip())
    planned: list[tuple[str, UnitFileKind]] = [
        *((m, "module") for m in unit.task.modules),
        *((c, "config") for c in unit.task.config_files),
    ]
    for raw, kind in planned:
        path = raw.strip()
        if path and path not in files:
            row = row_of(path) if kind == "module" else None
            responsibility = row.responsibility.strip() if row is not None else ""
            files[path] = UnitFileRef(path, kind, responsibility)
    return list(files.values())


@dataclass(frozen=True)
class LateDependency:
    """依存先が後の単位で作られる1件。`path`は依存する側のファイル、`dependency`は依存先の書き方
    (モジュール一覧の`depends_on`のまま)、`creator`は依存先を最初に作る単位の ID。"""

    unit: str
    path: str
    dependency: str
    creator: str


def late_dependencies(
    units: Sequence[tuple[str, Sequence[str]]],
    rows: Sequence[ModuleRow],
    row_of: RowOf,
    exempt: Set[str] = frozenset(),
) -> list[LateDependency]:
    """単位(計画の並び順の ID と、作るモジュールのパス)ごとに、依存先が後ろの単位で
    作られるものを返す。

    - 行の作り手は、その行に属するファイルを最初に作る単位。
    - 依存先は一覧の行に引く(`_refers_to`。ディレクトリの依存先は複数の行に当たる。
      そのうち最も早く作られる単位で見る)。一覧に無い依存先(外部のライブラリ)・どの単位も
      作らない行・自分の行は見ない。
    - 依存先を作る単位か、それより後の単位が、依存する側の行のファイルも変更するなら見ない
      (依存先を作るときに、依存する側へつなぐ形。ルートに後からサービスを足すなど)。
    - `exempt`の単位(基盤の単位)が作るファイルの依存は見ない(アプリの組み立てのように、後の
      機能の単位が自分のモジュールをつなぐ形が普通のため)。作り手としては数える。
    - 同じ単位・同じ依存先は1件にまとめる。
    """
    creators: dict[str, int] = {}
    last_touch: dict[str, int] = {}
    for position, (_, paths) in enumerate(units):
        for path in paths:
            row = row_of(path)
            if row is not None:
                creators.setdefault(row.path.strip(), position)
                last_touch[row.path.strip()] = position
    found: list[LateDependency] = []
    seen: set[tuple[str, str]] = set()
    for position, (uid, paths) in enumerate(units):
        if uid in exempt:
            continue
        for path in paths:
            row = row_of(path)
            if row is None:
                continue
            for dependency in (d.strip() for d in row.depends_on if d.strip()):
                built = [
                    creators[target.path.strip()]
                    for target in rows
                    if target.path.strip() != row.path.strip()
                    and target.path.strip() in creators
                    and _refers_to(dependency, target.path)
                ]
                if not built or min(built) <= position or (uid, dependency) in seen:
                    continue
                if last_touch[row.path.strip()] >= min(built):
                    continue
                seen.add((uid, dependency))
                found.append(LateDependency(uid, path, dependency, units[min(built)][0]))
    return found


def _refers_to(dependency: str, path: str) -> bool:
    """依存先が一覧の行を指すか。`module_ref_matches`に加えて、依存先の区切りのどれかが行の
    `*`でない区切りと等しいこと(`FastAPI`のような外部のライブラリが、`app/*`の`*`だけに
    当たらないように)。"""
    if not module_ref_matches(dependency, path):
        return False
    literal = {part for variant in path_variants(path) for part in variant if "*" not in part}
    return any(part in literal for variant in path_variants(dependency) for part in variant)


# テストのファイルのディレクトリ(パスの区切りのどれか)
_TEST_DIRECTORIES = frozenset({"test", "tests", "__tests__", "e2e"})
# テストのファイルの名前の形(最後の区切り)
_TEST_SUFFIXES = (
    "_test.py",
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
)


def is_test_path(path: str) -> bool:
    """パスがテストのファイルの形か(`tests/`・`__tests__/`の下、`test_*`・`*_test.py`・
    `*.test.ts(x)`・`*.spec.ts(x)`・`conftest.py`)。"""
    parts = [p for p in path.strip().strip("`").lower().split("/") if p]
    if not parts:
        return False
    name = parts[-1]
    return (
        any(p in _TEST_DIRECTORIES for p in parts[:-1])
        or name.startswith("test_")
        or name == "conftest.py"
        or name.endswith(_TEST_SUFFIXES)
    )


def has_test_file(doc: UnitProcedure) -> bool:
    """手順書のファイルに、テストのファイル(種類が`test`か、パスがテストの形)があるか。"""
    return any(f.kind == "test" or is_test_path(f.path) for f in doc.files)


# スキーマのファイル(テーブルを作るファイル)のパスの区切り。層の名前は見ない(「モデル」の層に
# セキュリティのファイルを置く設計もあるため)。`schemas/`は API の型(Pydantic)なので含めない
_SCHEMA_PARTS = frozenset(
    {"model", "models", "entity", "entities", "migration", "migrations", "alembic"}
)
_SCHEMA_SUFFIXES = (".sql", ".prisma")


def is_schema_path(path: str) -> bool:
    """パスがスキーマのファイル(ORM のモデル・マイグレーション・DDL)の形か。"""
    text = path.strip().strip("`").lower()
    if text.endswith(_SCHEMA_SUFFIXES):
        return True
    return any(part in _SCHEMA_PARTS for variant in path_variants(text) for part in variant)


def _squash(text: str) -> str:
    return text.strip().lower().replace("_", "").replace("-", "")


def _name_forms(table: str) -> set[str]:
    """テーブル名の照合の形(`_`・`-`を除いた小文字と、その単数形)。"""
    name = _squash(table)
    forms = {name}
    if name.endswith("ies") and len(name) > 3:
        forms.add(name[:-3] + "y")
    elif name.endswith("ses") and len(name) > 3:
        forms.add(name[:-2])
    elif name.endswith("s") and len(name) > 1:
        forms.add(name[:-1])
    return {form for form in forms if form}


def table_mentioned(table: str, texts: Sequence[str]) -> bool:
    """テーブル名(単数形も)が、どれかの文(パス・責務)に出てくるか。`chat_histories`は
    `chat_history.py`に、`users`は`app/models/user.py`に当たる。"""
    forms = _name_forms(table)
    squashed = [_squash(text) for text in texts]
    return any(form in text for form in forms for text in squashed)


@dataclass(frozen=True)
class TableCoverage:
    """テーブルを作る単位の判定。`schema_files`は単位が作るスキーマのファイル、`uncovered`は
    スキーマのファイルのパス・責務に名前の出ないテーブル(スキーマのファイルが無ければ全部、
    どのテーブルの名前も出ないスキーマのファイルがあれば無し)。"""

    schema_files: tuple[str, ...]
    uncovered: tuple[str, ...]


def table_coverage(tables: Sequence[str], files: Sequence[UnitFileRef]) -> TableCoverage:
    """設計のテーブルが、どれかの単位のスキーマのファイルで作られるかを判定する。"""
    schema = [f for f in files if is_schema_path(f.path)]
    names = list(dict.fromkeys(t.strip() for t in tables if t.strip()))
    texts = [[f.path, f.responsibility] for f in schema]
    # `models.py`・`orm.py`のように全テーブルをまとめたファイルは、テーブルの名前を持たない
    bundled = any(not any(table_mentioned(t, text) for t in names) for text in texts)
    flat = [text for pair in texts for text in pair if text]
    return TableCoverage(
        schema_files=tuple(dict.fromkeys(f.path for f in schema)),
        uncovered=() if schema and bundled else tuple(
            t for t in names if not table_mentioned(t, flat)
        ),
    )


# 観点の文が異常系を書いているとみなす言葉(ステータスの番号を書かない観点もあるため)
_ERROR_WORDS = re.compile(r"エラー|失敗|拒否|異常|不正")


def error_test_gap(statuses: Sequence[str], doc: UnitProcedure) -> tuple[str, ...]:
    """単位の API のエラー欄の HTTP ステータスがあるのに、手順書のテスト観点に異常系が無ければ、
    そのステータスすべて。異常系があるとみなすのは、観点・SUT・Given / When / Then にステータスの
    どれかが出るか、観点の文に異常系の言葉(`_ERROR_WORDS`)があるとき(ステータスごとの網羅は
    テスト仕様書が受け持つ)。"""
    if not statuses:
        return ()
    text = "\n".join([*(f"{t.viewpoint} {t.sut}" for t in doc.tests), *doc.gwt])
    if any(status in text for status in statuses):
        return ()
    if any(_ERROR_WORDS.search(t.viewpoint) for t in doc.tests):
        return ()
    return tuple(statuses)
