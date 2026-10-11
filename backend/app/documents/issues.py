"""上流の文書(要件定義書・外部設計書)の検証の指摘の型。指摘は警告だけで、生成も保存も止めない。"""

from dataclasses import dataclass
from typing import Literal

IssueLevel = Literal["critical", "major", "minor"]


@dataclass(frozen=True)
class DocumentIssue:
    level: IssueLevel
    code: str
    message: str
    targets: tuple[str, ...] = ()
