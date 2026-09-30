"""内部設計書(Markdown)から、UML図の生成に必要な節だけを決定的に取り出す純粋関数群。

AIに内部設計書の全文を渡すと、図ごとに不要な節まで入力トークンを消費する。図ごとに必要な節
(component: 3.1+3.3、ER: 3.2、DFD: 3.2+対象のDF節)だけを渡すため、見出しの固定形式
(app/services/doc_generator_service.pyの内部設計書プロンプトで指示している形式)を手がかりに
正規表現で切り出す。LLMは呼ばない(候補の列挙にクォータを消費せず、結果が決定的になる)。
"""

import re
from dataclasses import dataclass

# 内部設計書プロンプトが指示する「処理別データフロー」節の見出し
DFD_SECTION_TITLE = "処理別データフロー"

# `#### DF-1: POST /api/v1/reservations` 形式の見出し
_DFD_SUBJECT_HEADING = re.compile(r"^####\s+(DF-\d+)\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
# `### テーブル: reservations` 形式の見出し(3.2節のテーブル定義)
_ER_TABLE_HEADING = re.compile(r"^###\s+テーブル\s*[:：]\s*`?(.+?)`?\s*$", re.MULTILINE)


@dataclass(frozen=True)
class DfdSubject:
    """DFDの生成対象1件(APIエンドポイントまたはバッチ)。`title`が図の識別キー(subject)になる
    ── `code`(DF-n)は内部設計書を再生成すると振り直されうるため、キーには使わない。"""

    code: str
    title: str
    body: str


def extract_section(markdown: str, number: str) -> str:
    """`## <number>`(例: "3.2")で始まる節を、次の`#`/`##`見出しの直前まで取り出す。
    見つからない場合は空文字列を返す。"""
    lines = markdown.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if start is None:
            if re.match(rf"^##\s+{re.escape(number)}(\s|$)", line):
                start = index
        elif re.match(r"^#{1,2}\s", line):
            return "\n".join(lines[start:index]).strip()
    return "\n".join(lines[start:]).strip() if start is not None else ""


def remove_subsection(section: str, title: str) -> str:
    """節の中から`### <title>`で始まる小節を、次の`###`以上の見出しの直前まで取り除く。"""
    lines = section.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        if re.match(rf"^###\s+{re.escape(title)}\s*$", line):
            skipping = True
            continue
        if skipping and re.match(r"^#{1,3}\s", line):
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip()


def extract_dfd_subjects(markdown: str) -> list[DfdSubject]:
    """`#### DF-<n>: <処理名>`の見出しを列挙し、見出しごとの本文(次の`####`以上の見出しの
    直前まで)と組にして返す。旧形式の内部設計書(Phase 10以前に生成したもの)では空リストになる。"""
    matches = list(_DFD_SUBJECT_HEADING.finditer(markdown))
    subjects: list[DfdSubject] = []
    for match in matches:
        rest = markdown[match.end() :]
        next_heading = re.search(r"^#{1,4}\s", rest, re.MULTILINE)
        body = rest[: next_heading.start()] if next_heading else rest
        subjects.append(DfdSubject(code=match.group(1), title=match.group(2), body=body.strip()))
    return subjects


def extract_er_tables(markdown: str) -> list[str]:
    """3.2節の`### テーブル: <テーブル名>`の見出しからテーブル名を列挙する。"""
    return [m.group(1) for m in _ER_TABLE_HEADING.finditer(extract_section(markdown, "3.2"))]


def extract_er_table_blocks(markdown: str, table_names: list[str]) -> str:
    """3.2節から、指定したテーブルの見出しと本文だけを連結して返す(ER部分図の入力)。"""
    section = extract_section(markdown, "3.2")
    wanted = set(table_names)
    blocks: list[str] = []
    for match in _ER_TABLE_HEADING.finditer(section):
        if match.group(1) not in wanted:
            continue
        rest = section[match.end() :]
        next_heading = re.search(r"^#{1,3}\s", rest, re.MULTILINE)
        body = rest[: next_heading.start()] if next_heading else rest
        blocks.append(f"{match.group(0).strip()}\n{body.strip()}")
    return "\n\n".join(blocks)


def find_section_heading_end(markdown: str, number: str) -> int | None:
    """`## <number>`(例: "3.2")の見出し行の直後(改行の後ろ)の位置を返す。無ければNone。
    `extract_section`と同じ見出しの判定を使う。"""
    match = re.search(rf"^##\s+{re.escape(number)}(\s.*)?$", markdown, re.MULTILINE)
    return _line_end(markdown, match.end()) if match else None


def find_dfd_heading_end(markdown: str, title: str) -> int | None:
    """`#### DF-<n>: <title>`の見出し行の直後の位置を返す。無ければNone。
    DF-nの番号は再生成で振り直されうるため、`extract_dfd_subjects`と同じく処理名(title)で探す。"""
    for match in _DFD_SUBJECT_HEADING.finditer(markdown):
        if match.group(2) == title:
            return _line_end(markdown, match.end())
    return None


def _line_end(markdown: str, index: int) -> int:
    """`index`を含む行の改行の直後(最終行なら文字列の末尾)。"""
    newline = markdown.find("\n", index)
    return len(markdown) if newline == -1 else newline + 1
