"""段階5の手順から導くシーケンス図のモデルと、Mermaid への変換(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」の「シーケンス図」。

シーケンス図は、段階5の手順の表の別の見え方で、保存しない。正本は手順の表のままで、図は直さない
(直すのは表)。LLM を使わず、同じ手順からは常に同じ図を導く。

- 参加者(ライフライン)は呼び出し元・呼び出し先に現れた順。`id`(`P1`…)は Mermaid の別名に使う
  (パスの「/」を避けるため)。
- 矢印の種類は行の種別(`kind`)。表に無い入れ子と戻りは、呼び出し中の参加者の積み上げ(スタック)で
  推測する。推測できないところは指摘(`SequenceIssue`)にし、段階5の検証の警告になる。
- 分岐の行は、元の手順の矢印に付けた注記として描く。ループ・並行は扱わない。
- テスト観点との突き合わせ(`reachable_callees`・`sut_participant`・`stubs_outside_sequence`)は、
  手順書のスタブの欄が、手順に無い依存を挙げていないかを段階8の検証が調べるのに使う。
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.detailed_design.procedure import (
    Procedure,
    StepKind,
    is_external_actor,
    number_steps,
    step_id,
)
from app.detailed_design.structure import ModuleListModel, module_ref_matches

# 図にするときの指摘の種類(段階5の検証の警告のコード)
SequenceIssueCode = Literal[
    "RETURN_AS_CALL",
    "NESTING_UNKNOWN",
    "MISSING_BRANCH_TARGET",
    "CALLEE_NOT_DEPENDENCY",
    "EMPTY_CALLER",
]

# 分岐の欄の「1a へ」(分岐先の手順番号)
_BRANCH_REF = re.compile(r"([0-9]+[a-z]*)\s*へ")


@dataclass(frozen=True)
class Participant:
    """図の参加者(ライフライン)。`name`はモジュールのパスか外部の役者の名前。"""

    id: str
    name: str


@dataclass(frozen=True)
class SequenceMessage:
    """矢印1本。`step_id`は手順ID(`F-07#2`)。表に無く推測した戻りは`derived`で、どの呼び出しの
    戻りかを`step_id`に持つ。`source`・`target`は参加者の`id`。"""

    step_id: str
    source: str
    target: str
    kind: StepKind
    label: str
    derived: bool = False


@dataclass(frozen=True)
class SequenceNote:
    """分岐の行の注記。`over`は元の手順の矢印の両端の参加者の`id`。"""

    step_id: str
    over: tuple[str, ...]
    text: str


SequenceEvent = SequenceMessage | SequenceNote


@dataclass(frozen=True)
class SequenceIssue:
    """図にするときの指摘1つ(手順の表の書き方の問題)。"""

    step_id: str
    code: SequenceIssueCode
    message: str


@dataclass(frozen=True)
class SequenceDiagram:
    """1つの処理のシーケンス図のモデル。"""

    function_id: str
    participants: tuple[Participant, ...] = ()
    events: tuple[SequenceEvent, ...] = ()
    issues: tuple[SequenceIssue, ...] = ()

    def name_of(self, participant_id: str) -> str:
        return next(p.name for p in self.participants if p.id == participant_id)


@dataclass
class _Frame:
    """呼び出し中の参加者と、その呼び出しの手順ID・結果(推測した戻りのラベルにする)。"""

    name: str
    step_id: str
    result: str


def module_dependencies(modules: ModuleListModel | None) -> dict[str, list[str]]:
    """段階4のモジュール一覧から、パス → 依存先(`to_sequence`の入力。未承認なら空)。"""
    if modules is None:
        return {}
    return {row.path.strip(): list(row.depends_on) for row in modules.modules}


def _depends_on(
    dependencies: Mapping[str, Sequence[str]], caller: str, callee: str
) -> bool | None:
    """呼び出し先が、呼び出し元の依存先(段階4)にあるか。呼び出し元がモジュール一覧に無ければ None
    (判断しない)。"""
    if caller not in dependencies:
        return None
    return any(
        ref.strip() == callee or module_ref_matches(ref, callee) for ref in dependencies[caller]
    )


def to_sequence(
    procedure: Procedure, dependencies: Mapping[str, Sequence[str]] | None = None
) -> SequenceDiagram:
    """手順をシーケンス図のモデルに変える。`dependencies`は段階4のパス → 依存先(省略すると依存先の
    指摘をしない)。

    - 呼び出し元がスタックの途中にいれば、その上の参加者は戻ったとみなし、戻りを推測で足す。
    - 種別が戻りの行は、呼び出し先(値を受け取る側)まで戻る。種別が同期の呼び出しなのに呼び出し先が
      スタックの下(呼び出し元の呼び出し元)にいれば、戻りを呼び出しとして書いているとみなし、
      戻りとして描いて指摘する(`RETURN_AS_CALL`)。非同期の呼び出し(通知など)は、呼び出し中の
      参加者へ向けてもよい。
    - 呼び出し元が呼び出し中でなければ、入れ子を推測できないと指摘し、新しい流れとして描く。
    - 非同期の呼び出しは戻りを待たない(スタックに積まない)。同じ参加者の中の呼び出しも積まない
      (図では輪で描き、戻りを描かない)。
    - 呼び出し先が呼び出し元の依存先(段階4)に無ければ指摘する(外部の役者・同じモジュールは除く)。
    - 分岐の欄の「1a へ」が存在しない行を指していれば指摘する。
    - 呼び出し先が空の行は描かない(段階5の検証のエラー`EMPTY_CALLEE`で直させる)。
    """
    deps = dependencies or {}
    participants: list[Participant] = []
    ids: dict[str, str] = {}

    def ensure(name: str) -> str:
        if name not in ids:
            ids[name] = f"P{len(participants) + 1}"
            participants.append(Participant(ids[name], name))
        return ids[name]

    events: list[SequenceEvent] = []
    issues: list[SequenceIssue] = []
    numbers = number_steps(procedure.steps)
    existing = set(numbers)
    stack: list[_Frame] = []
    last_pair: tuple[str, ...] = ()

    def pop_until(name: str) -> None:
        """`name`がスタックの一番上になるまで戻り(推測)を足す。"""
        while len(stack) > 1 and stack[-1].name != name:
            done = stack.pop()
            events.append(
                SequenceMessage(
                    done.step_id,
                    ensure(done.name),
                    ensure(stack[-1].name),
                    "return",
                    done.result,
                    derived=True,
                )
            )

    def in_stack(name: str) -> bool:
        return any(frame.name == name for frame in stack)

    for step, number in zip(procedure.steps, numbers, strict=True):
        sid = step_id(procedure.function_id, number)
        for match in _BRANCH_REF.finditer(step.branch):
            if match.group(1) not in existing:
                message = f"手順 {sid} の分岐「{match.group(0)}」の行がありません。"
                issues.append(SequenceIssue(sid, "MISSING_BRANCH_TARGET", message))
        if step.is_branch:
            text = " → ".join(t for t in (step.action.strip(), step.branch.strip()) if t)
            events.append(SequenceNote(sid, last_pair, text))
            continue
        caller, callee = step.caller.strip(), step.callee.strip()
        if not callee:
            continue
        if not caller:
            message = f"手順 {sid} の呼び出し元が空です(図に描けません)。"
            issues.append(SequenceIssue(sid, "EMPTY_CALLER", message))
            continue

        if not stack:
            stack.append(_Frame(caller, sid, ""))
        elif not in_stack(caller):
            message = (
                f"手順 {sid} の呼び出し元 {caller} が呼び出し中ではありません"
                "(入れ子を推測できないので、新しい流れとして描きます)。"
            )
            issues.append(SequenceIssue(sid, "NESTING_UNKNOWN", message))
            pop_until(stack[0].name)
            stack.clear()
            stack.append(_Frame(caller, sid, ""))
        else:
            pop_until(caller)

        returning = step.kind == "return"
        if step.kind == "call" and callee != caller and in_stack(callee):
            returning = True
            message = (
                f"手順 {sid} の呼び出し先 {callee} は呼び出し元の側にいます"
                "(戻りを呼び出しとして書いているので、戻りとして描きます。種別を「戻り」にします)。"
            )
            issues.append(SequenceIssue(sid, "RETURN_AS_CALL", message))
        if returning:
            # 呼び出し先まで戻る。最後の戻りに、この行のデータを載せる
            while len(stack) > 1 and stack[-2].name != callee:
                pop_until(stack[-2].name)
            done = stack.pop() if len(stack) > 1 else stack[0]
            label = step.data.strip() or step.result.strip()
            events.append(SequenceMessage(sid, ensure(done.name), ensure(callee), "return", label))
            last_pair = (ensure(done.name), ensure(callee))
            continue

        external = is_external_actor(callee)
        if callee != caller and not external and _depends_on(deps, caller, callee) is False:
            message = (
                f"手順 {sid} の呼び出し先 {callee} が、呼び出し元 {caller} の"
                "依存先(段階4)にありません。"
            )
            issues.append(SequenceIssue(sid, "CALLEE_NOT_DEPENDENCY", message))
        call, data = step.call.strip(), step.data.strip()
        label = f"{call}({data})" if call and data else call or data or step.action.strip()
        events.append(SequenceMessage(sid, ensure(caller), ensure(callee), step.kind, label))
        last_pair = (ensure(caller), ensure(callee))
        if step.kind != "async" and callee != caller:
            stack.append(_Frame(callee, sid, step.result.strip()))
    if stack:
        pop_until(stack[0].name)
    return SequenceDiagram(
        procedure.function_id, tuple(participants), tuple(events), tuple(issues)
    )


# Mermaid の矢印(同期 = 実線の矢じり、非同期 = 開いた矢じり、戻り = 破線)
_ARROWS: dict[StepKind, str] = {"call": "->>", "async": "-)", "return": "-->>"}


def _mermaid_text(text: str) -> str:
    """Mermaid で意味を持つ文字(「#」は文字参照、「;」は文の区切り)と改行を外す。"""
    return " ".join(re.sub(r"[;#]", " ", text).split())


def to_mermaid(diagram: SequenceDiagram) -> str:
    """Mermaid の`sequenceDiagram`のテキスト(md・AI 向けの版に入れる)。矢印の先頭に手順番号
    (手順ID の「#」の後ろ)を付け、手順の表の行と対応させる。"""
    lines = ["sequenceDiagram"]
    for p in diagram.participants:
        lines.append(f"  participant {p.id} as {_mermaid_text(p.name)}")
    for event in diagram.events:
        number = event.step_id.split("#", 1)[1]
        if isinstance(event, SequenceNote):
            if event.over:
                text = _mermaid_text(event.text)
                lines.append(f"  Note over {','.join(event.over)}: {number} {text}")
            continue
        prefix = f"({number} の戻り)" if event.derived else f"{number}:"
        label = _mermaid_text(event.label)
        arrow = _ARROWS[event.kind]
        lines.append(f"  {event.source}{arrow}{event.target}: {prefix} {label}".rstrip())
    return "\n".join(lines)


def reachable_callees(diagram: SequenceDiagram, start: str) -> list[str]:
    """参加者`start`(名前)から、呼び出し(戻り以外)をたどって届く参加者の名前(図の並び順)。
    SUT から見たスタブの候補になる。"""
    if start not in {p.name for p in diagram.participants}:
        return []
    edges: dict[str, set[str]] = {}
    for event in diagram.events:
        if isinstance(event, SequenceMessage) and event.kind != "return":
            edges.setdefault(event.source, set()).add(event.target)
    origin = next(p.id for p in diagram.participants if p.name == start)
    seen: set[str] = set()
    queue = [origin]
    while queue:
        for nxt in sorted(edges.get(queue.pop(0), set())):
            if nxt != origin and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return [p.name for p in diagram.participants if p.id in seen]


def sut_participant(diagram: SequenceDiagram, sut: str, trigger: str) -> str | None:
    """テスト観点の SUT(関数名か、トリガーの「メソッド パス」)を、図の参加者の名前に対応させる。
    関数名ならそれを呼ぶ矢印の先、トリガーなら最初の呼び出しの先。対応しなければ None。"""
    name = sut.strip()
    calls = [e for e in diagram.events if isinstance(e, SequenceMessage) and e.kind != "return"]
    if not name or not calls:
        return None
    if name == trigger.strip():
        return diagram.name_of(calls[0].target)
    hit = next((e for e in calls if e.label.split("(", 1)[0] == name), None)
    return diagram.name_of(hit.target) if hit is not None else None


def _loose_name(path: str) -> str:
    """パスを、テスト観点の文章と突き合わせる形にする(`services/ai_service.py` → `aiservice`)。"""
    base = path.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"[_-]", "", re.sub(r"\.[A-Za-z]+$", "", base)).lower()


def stubs_outside_sequence(
    stub_text: str, candidates: Sequence[str], module_paths: Sequence[str]
) -> list[str]:
    """スタブの欄が挙げるモジュールのうち、図から導いた候補(SUT から呼ぶ先)の外にあるもの
    (モジュール一覧の並び)。挙げていれば、手順に無い依存をテストで差し替えようとしている
    (手順か観点のどちらかが足りない)。候補なのにスタブに無いものは、本物を使う(結合テストなど)ので
    指摘しない。"""
    text = re.sub(r"[_-]", "", stub_text).lower()
    allowed = {_loose_name(name) for name in candidates}
    return [
        path
        for path in module_paths
        if (name := _loose_name(path)) and name in text and name not in allowed
    ]
