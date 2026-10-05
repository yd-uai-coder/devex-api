"""文書(Markdown)から、番号付きの節を決定的に取り出す純粋関数。

詳細設計モードの段階1が、外部設計書の「2.6 API一覧」を読むのに使う
(app/detailed_design/api_list.py)。
LLMは呼ばない。
"""

import re


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
