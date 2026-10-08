"""簡易ドキュメントモードの実装計画書「4.2 タスク分解(WBS)」の解析(純粋関数)。

docs/internal_design.md 3.3節「5. 実装手順書」の簡易モード。

簡易モードには段階7が無いので、実装手順書の作業単位は実装計画書の WBS から取る。WBS の書式は
実装計画書のプロンプト(app/services/doc_generator_service.py)で決まった形に固定し、ここで
段階7と同じ`PlanModel`に決定的に読み替える(AI で抽出しない。文書の ID と手順書の ID を
必ず一致させるため)。

    ### M-01: 予約の登録 ── 【Must】
    - ゴール: 備品を予約して一覧で確かめられる
    - [ ] M-01-T01 [基盤] 開発環境とDBを用意する
      - モジュール: backend/app/main.py
      - 環境・設定: docker-compose.yml
    - [ ] M-01-T02 [機能] 予約を登録する
      - 処理: DF-1
      - 依存: M-01-T01

- 単位の ID は、詳細設計モードと同じく並び順から導く(`task_id`)。書かれた ID が並び順と
  違えば指摘し、依存先は書かれた ID から導いた ID に読み替える。
- 読めない行は捨てて指摘にする(直す先は実装計画書。文書は画面で編集できないので再生成)。
  見出し・区切りの揺れ(全角のコロン・太字・`──`と`--`)は許す。
- 処理(DF)・依存先・モジュールが設計にあるかは、ここでは見ない(内部設計書と突き合わせる
  段階8の検証が見る)。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.detailed_design.plan import (
    Milestone,
    PlanModel,
    PlanTask,
    Priority,
    UnitKind,
    task_id,
)
from app.detailed_design.procedure_doc import DesignDocument, FindingLevel
from app.uml.generation.sections import extract_section

WBS_SECTION = "4.2"
ENVIRONMENT_SECTION = "4.3"

_MILESTONE = re.compile(
    r"^###\s+\**\s*(M-\d+)\s*\**\s*[:：]\s*(.+?)\s*"
    r"(?:(?:──|—|--|-)\s*【\s*(Must|Should|Could|Won'?t)[^】]*】)?\s*\**\s*$",
    re.IGNORECASE,
)
_GOAL = re.compile(r"^[-*]\s*\**ゴール\**\s*[:：]\s*(.*)$")
_TASK = re.compile(
    r"^[-*]\s*\[[ xX]\]\s*\**\s*(M-\d+-T\d+)\s*\**\s*[\[［【]\s*(機能|基盤)\s*[\]］】]\s*(.+)$"
)
_CHECKBOX = re.compile(r"^[-*]\s*\[[ xX]\]")
_ATTRIBUTE = re.compile(r"^[-*]\s*\**(処理|依存|モジュール|環境・設定)\**\s*[:：]\s*(.*)$")
_EMPTY_VALUES = {"なし", "無し", "—", "-", "－", "n/a"}
_KINDS: dict[str, UnitKind] = {"機能": "feature", "基盤": "base"}
_PRIORITIES: dict[str, Priority] = {"must": "Must", "should": "Should", "could": "Could"}


@dataclass(frozen=True)
class WbsIssue:
    """WBS の解析の指摘1つ。`unit`は指摘の出た単位の(導いた)ID。"""

    code: str
    message: str
    level: FindingLevel
    fix_document: DesignDocument = "implementation_plan"
    target: str | None = None
    unit: str | None = None


@dataclass(frozen=True)
class WbsParse:
    """解析の結果。`plan`は段階7と同じ形(横断事項・リスクは持たない)。"""

    plan: PlanModel
    issues: tuple[WbsIssue, ...] = ()


@dataclass
class _Task:
    written_id: str
    kind: UnitKind
    title: str
    function_ids: list[str]
    depends_on: list[str]
    modules: list[str]
    config_files: list[str]


@dataclass
class _Milestone:
    name: str
    priority: Priority
    goal: str
    tasks: list[_Task]


def parse_wbs(markdown: str) -> WbsParse:
    """実装計画書の 4.2 節を`PlanModel`に読み替える(開発環境は 4.3 節の本文)。"""
    section = extract_section(markdown, WBS_SECTION)
    environment = _body(extract_section(markdown, ENVIRONMENT_SECTION))
    if not section:
        issue = WbsIssue(
            "WBS_MISSING",
            "実装計画書に「4.2 タスク分解(WBS)」がありません(実装計画書を再生成してください)。",
            "critical",
        )
        return WbsParse(PlanModel(environment=environment), (issue,))

    issues: list[WbsIssue] = []
    milestones: list[_Milestone] = []
    current: _Milestone | None = None
    task: _Task | None = None
    skipping = False
    old_checkboxes = 0
    for raw in section.splitlines()[1:]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            task = None
            current, skipping = _milestone(line, issues)
            if current is not None:
                milestones.append(current)
            continue
        if skipping:
            continue
        if (match := _TASK.match(line)) is not None:
            if current is None:
                issues.append(_format(f"マイルストーンの見出しの前にタスクがあります: {line}"))
                task = None
                continue
            written_id, kind, title = match.groups()
            task = _Task(written_id, _KINDS[kind], _plain(title), [], [], [], [])
            current.tasks.append(task)
            continue
        if _CHECKBOX.match(line):
            old_checkboxes += 1
            issues.append(_format(f"タスクの行が書式に合いません(ID と[機能]/[基盤]): {line}"))
            task = None
            continue
        if (match := _ATTRIBUTE.match(line)) is not None and task is not None:
            _set_attribute(task, match.group(1), match.group(2))
            continue
        if (match := _GOAL.match(line)) is not None and current is not None and task is None:
            current.goal = _plain(match.group(1))
            continue
        if raw[:1] in "-*" and task is None and current is not None:
            issues.append(_format(f"読めない行があります: {line}"))

    plan, renumbered = _to_plan(milestones, environment)
    issues += renumbered
    if not plan.milestones or not any(m.tasks for m in plan.milestones):
        message = (
            "実装計画書の WBS が古い形式です(タスクに ID がありません)。"
            "実装計画書を再生成してください。"
            if old_checkboxes
            else "実装計画書の WBS にタスクがありません(実装計画書を再生成してください)。"
        )
        # 単位が1つも読めなければ、行ごとの指摘は出さない(文書ごと作り直すため)
        return WbsParse(plan, (WbsIssue("WBS_MISSING", message, "critical"),))
    return WbsParse(plan, tuple(issues))


def _milestone(line: str, issues: list[WbsIssue]) -> tuple[_Milestone | None, bool]:
    """見出し1行を読む。戻り値はマイルストーン(読めなければ None)と、その中を飛ばすか。"""
    match = _MILESTONE.match(line)
    if match is None:
        issues.append(_format(f"マイルストーンの見出しが書式に合いません: {line}"))
        return None, False
    _, name, priority = match.groups()
    normalized = (priority or "").lower().replace("'", "")
    if normalized == "wont":
        message = f"Won't のマイルストーンはタスクにしません(読み飛ばしました): {line}"
        issues.append(WbsIssue("WBS_FORMAT", message, "minor"))
        return None, True
    if not priority:
        message = f"マイルストーンの見出しに優先度がありません(Must とみなします): {line}"
        issues.append(_format(message))
    return _Milestone(_plain(name), _PRIORITIES.get(normalized, "Must"), "", []), False


def _set_attribute(task: _Task, name: str, value: str) -> None:
    values = _values(value)
    if name == "処理":
        task.function_ids += values
    elif name == "依存":
        task.depends_on += values
    elif name == "モジュール":
        task.modules += values
    else:
        task.config_files += values


def _to_plan(milestones: list[_Milestone], environment: str) -> tuple[PlanModel, list[WbsIssue]]:
    """並び順から ID を導き、書かれた ID との違いを指摘し、依存先を導いた ID に読み替える。"""
    issues: list[WbsIssue] = []
    renames: dict[str, str] = {}
    for m, milestone in enumerate(milestones):
        for t, task in enumerate(milestone.tasks):
            derived = task_id(m, t)
            renames.setdefault(task.written_id, derived)
            if task.written_id != derived:
                message = (
                    f"タスク「{task.title}」の ID {task.written_id} が、並び順の ID {derived} と"
                    f"違います({derived} として読みます)。"
                )
                issues.append(WbsIssue("WBS_ID_MISMATCH", message, "minor", unit=derived))
    plan = PlanModel(
        milestones=[
            Milestone(
                name=milestone.name,
                goal=milestone.goal,
                priority=milestone.priority,
                tasks=[
                    PlanTask(
                        kind=task.kind,
                        title=task.title,
                        function_ids=task.function_ids,
                        depends_on=[renames.get(d, d) for d in task.depends_on],
                        modules=task.modules,
                        config_files=task.config_files,
                    )
                    for task in milestone.tasks
                ],
            )
            for milestone in milestones
        ],
        environment=environment,
    )
    return plan, issues


def _values(text: str) -> list[str]:
    """欄の値を区切り(`,`・`、`)で分け、`` ` ``・太字と「なし」を除く。"""
    items = (_plain(v) for v in re.split(r"[,、，]", text))
    return _unique(v for v in items if v and v.lower() not in _EMPTY_VALUES)


def _plain(text: str) -> str:
    return text.replace("**", "").strip().strip("`").strip()


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _body(section: str) -> str:
    """節の見出し行を除いた本文。"""
    return "\n".join(section.splitlines()[1:]).strip()


def _format(message: str) -> WbsIssue:
    return WbsIssue("WBS_FORMAT", message, "major")
