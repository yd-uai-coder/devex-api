"""外部設計書の「2.6 API一覧」の表を読み取る純粋関数(docs/external_design.md 2.7節 段階1)。

段階1(機能一覧)は、AIが外部設計書から処理を下書きし、機能グループの初期値をAPIのパスから
決定的に作る。この表は、その「パス」の出どころであり、AIの下書きに漏れたAPIを見つける照合の
基準でもある。表の形は app/services/doc_generator_service.py の外部設計書プロンプトで指示して
いる(メソッド/パス/概要/関連画面)。LLMは呼ばない。

照合のキー(`endpoint_key`)は、メソッドを大文字にし、パスの波括弧の中身(`{id}`・`{project_id}`
など、書き手で揺れる名前)を`{}`にそろえたもの。AIの下書きと外部設計書で引数名が違っても、
同じAPIとして突き合わせられる。
"""

import re
from dataclasses import dataclass

from app.uml.generation.sections import extract_section

API_LIST_SECTION = "2.6"

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# `POST /api/v1/projects` の形(後ろに「(BackgroundTasks)」などの注記が続いてもよい)
_TRIGGER = re.compile(rf"^\s*({'|'.join(HTTP_METHODS)})\s+(/[^\s(（]*)", re.IGNORECASE)
_PATH_PARAM = re.compile(r"\{[^}]*\}")
# 表の区切り行(|---|:---:|)
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


@dataclass(frozen=True)
class ApiEndpoint:
    """外部設計書のAPI一覧の1行。"""

    method: str
    path: str
    summary: str
    screens: tuple[str, ...]

    @property
    def key(self) -> str:
        return endpoint_key(self.method, self.path)


def normalize_path(path: str) -> str:
    """パスの比較用の形。バッククォートと末尾の`/`を除き、波括弧の中身を空にする。"""
    path = path.strip().strip("`").rstrip("/") or "/"
    return _PATH_PARAM.sub("{}", path)


def endpoint_key(method: str, path: str) -> str:
    return f"{method.strip().upper()} {normalize_path(path)}"


def parse_trigger(trigger: str) -> tuple[str, str] | None:
    """トリガーの文字列(`POST /api/v1/projects/{id}/generate(BackgroundTasks)`)から
    (メソッド, パス)を取り出す。APIでないトリガー(バッチ名など)は`None`。"""
    match = _TRIGGER.match(trigger.strip().strip("`"))
    if match is None:
        return None
    return match.group(1).upper(), match.group(2).strip("`")


def trigger_key(trigger: str) -> str | None:
    """トリガーがAPIなら照合のキーを、そうでなければ`None`を返す。"""
    parsed = parse_trigger(trigger)
    return endpoint_key(*parsed) if parsed is not None else None


def extract_api_endpoints(markdown: str) -> list[ApiEndpoint]:
    """外部設計書の2.6節の表から、APIを表の順に取り出す。節や表が無ければ空。

    列は見出しの名前で探す(「メソッド」「パス」は必須、「概要」「関連画面」は任意)。
    同じキーのAPIが2行あれば、最初の行だけを残す。"""
    section = extract_section(markdown, API_LIST_SECTION)
    endpoints: list[ApiEndpoint] = []
    seen: set[str] = set()
    columns: dict[str, int] | None = None
    for line in section.splitlines():
        cells = _cells(line)
        if cells is None:
            columns = None  # 表の外に出た
            continue
        if columns is None:
            columns = _header_columns(cells)
            continue
        if all(_SEPARATOR_CELL.match(cell) for cell in cells if cell):
            continue
        endpoint = _endpoint_from_row(cells, columns)
        if endpoint is not None and endpoint.key not in seen:
            seen.add(endpoint.key)
            endpoints.append(endpoint)
    return endpoints


def _cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _header_columns(cells: list[str]) -> dict[str, int] | None:
    """見出し行から列の位置を決める。メソッドとパスの列が無い表は対象外(`None`)。"""
    names = {"method": "メソッド", "path": "パス", "summary": "概要", "screens": "関連画面"}
    columns = {
        key: index
        for key, label in names.items()
        for index, cell in enumerate(cells)
        if label in cell
    }
    if "method" not in columns or "path" not in columns:
        return None
    return columns


def _endpoint_from_row(cells: list[str], columns: dict[str, int]) -> ApiEndpoint | None:
    def cell(key: str) -> str:
        index = columns.get(key)
        return cells[index] if index is not None and index < len(cells) else ""

    method = cell("method").strip("`").upper()
    path = cell("path").strip("`")
    if method not in HTTP_METHODS or not path.startswith("/"):
        return None
    screens = tuple(
        screen.strip()
        for screen in re.split(r"[/、,]", cell("screens"))
        if screen.strip() and screen.strip() not in ("—", "-", "なし")
    )
    return ApiEndpoint(method=method, path=path, summary=cell("summary"), screens=screens)
