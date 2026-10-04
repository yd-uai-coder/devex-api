"""段階ごとの内容の検証(純粋関数)。

承認の条件は、全段階に共通の3つ(段階が開いている・版が一致する・承認できる状態で内容が空でない。
app/services/design_stage_service.py)に加えて、段階ごとの検証で「エラー」が無いこと。警告は承認を
止めない(UML図の検証と同じ考え方。app/uml/validation/)。保存は検証の結果によらず通す(編集の
途中の状態も保存できるようにするため)。

段階ごとの検証は`STAGE_VALIDATORS`に登録する。今は段階1・2で、段階3以降は各段階の Phase で
足す(登録の無い段階は検証なし)。検証には段階の内容のほかに入力の文書の本文が要ることがあるので、
`StageSources`で渡す(段階1は外部設計書のAPI一覧と照らして、下書きの漏れを警告する)。
段階2は、入力の段階1の内容と、機能グループの DFD(`uml_diagrams`)の要約も使う(Phase 17)。
"""

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from app.detailed_design.api_list import extract_api_endpoints, trigger_key
from app.detailed_design.data_flow import (
    APPROVED_DIAGRAM_STATUSES,
    MAX_DFD_GROUPS,
    DataFlowModel,
    dfd_subject,
    group_functions,
)
from app.detailed_design.function_list import FunctionListModel, function_number

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class StageIssue:
    """検証の指摘1件。`target`は指摘の対象(処理ID・機能グループ名など。無ければNone)。"""

    severity: Severity
    code: str
    message: str
    target: str | None = None


@dataclass(frozen=True)
class DfdDiagramSummary:
    """段階2の検証に使う、機能グループの DFD 1枚の要約(`uml_diagrams`の行から作る)。"""

    status: str
    generation_status: str
    process_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageSources:
    """検証・下書きの生成に使う入力。

    - `documents`: 入力の文書の本文(doc_type → 表示中の版の本文)。
    - `stages`: 入力の段階の内容(段階番号 → 承認済みの段階の`model`)。
    - `dfd_diagrams`: 機能グループの DFD の要約(subject → 要約)。段階2が使う。
    """

    documents: Mapping[str, str] = field(default_factory=dict)
    stages: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
    dfd_diagrams: Mapping[str, DfdDiagramSummary] = field(default_factory=dict)


StageValidator = Callable[[Mapping[str, Any], StageSources], list[StageIssue]]


def has_errors(issues: list[StageIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def validate_function_list(model: Mapping[str, Any], sources: StageSources) -> list[StageIssue]:
    """段階1(機能一覧)の検証。

    エラー: 形が不正 / 処理が0件 / 処理IDの形式・重複・`next_number`以上の番号 / 名称が空 /
    機能グループが一覧に無い / 機能グループの重複。
    警告: 使われていない機能グループ / トリガーの重複 /
    外部設計書のAPI一覧にあるが機能一覧に無いAPI。
    """
    try:
        parsed = FunctionListModel.model_validate(model)
    except ValidationError as exc:
        return [_error("INVALID_MODEL", f"機能一覧の形が正しくありません: {exc}")]

    issues: list[StageIssue] = []
    if not parsed.functions:
        issues.append(_error("EMPTY_FUNCTIONS", "処理が1件もありません。"))

    for group, count in Counter(parsed.groups).items():
        if count > 1:
            message = f"機能グループ「{group}」が重複しています。"
            issues.append(_error("DUPLICATE_GROUP", message, group))

    id_counts = Counter(row.id for row in parsed.functions)
    for row in parsed.functions:
        number = function_number(row.id)
        if number is None:
            message = f"処理ID「{row.id}」の形式が F-01 ではありません。"
            issues.append(_error("INVALID_FUNCTION_ID", message, row.id))
        elif number >= parsed.next_number:
            message = (
                f"処理ID「{row.id}」は、まだ振り出していない番号です"
                f"(次に振る番号は {parsed.next_number})。"
            )
            issues.append(_error("FUNCTION_ID_NOT_ISSUED", message, row.id))
        if id_counts[row.id] > 1:
            message = f"処理ID「{row.id}」が重複しています。"
            issues.append(_error("DUPLICATE_FUNCTION_ID", message, row.id))
        if not row.name.strip():
            issues.append(_error("EMPTY_NAME", f"{row.id} の名称が空です。", row.id))
        if row.group not in parsed.groups:
            message = f"{row.id} の機能グループ「{row.group}」が、機能グループの一覧にありません。"
            issues.append(_error("UNKNOWN_GROUP", message, row.id))

    used_groups = {row.group for row in parsed.functions}
    for group in dict.fromkeys(parsed.groups):
        if group not in used_groups:
            message = f"機能グループ「{group}」に処理がありません。"
            issues.append(_warning("UNUSED_GROUP", message, group))

    trigger_owners: dict[str, list[str]] = {}
    for row in parsed.functions:
        key = trigger_key(row.trigger)
        if key is not None:
            trigger_owners.setdefault(key, []).append(row.id)
    for key, owners in trigger_owners.items():
        if len(owners) > 1:
            message = f"{'・'.join(owners)} のトリガー({key})が同じです。"
            issues.append(_warning("DUPLICATE_TRIGGER", message, owners[0]))

    external_design = sources.documents.get("external_design", "")
    for endpoint in extract_api_endpoints(external_design):
        if endpoint.key not in trigger_owners:
            api = f"{endpoint.method} {endpoint.path}"
            message = f"外部設計書のAPI一覧にある {api} が、機能一覧にありません。"
            issues.append(_warning("MISSING_API", message, api))
    return issues


def validate_data_flow(model: Mapping[str, Any], sources: StageSources) -> list[StageIssue]:
    """段階2(データフロー)の検証。

    エラー: 形が不正 / DFD を描くグループが上限を超える・重複・機能一覧に無い /
    処理概要表の処理IDが機能一覧に無い・重複 / 処理概要表に無い処理 /
    選んだグループの DFD が無い・生成中・未承認。
    警告: 処理概要表の入力・処理内容・出力が空 / DFD にそのグループでない処理がある /
    DFD に描かれていないグループの処理がある。
    """
    try:
        parsed = DataFlowModel.model_validate(model)
    except ValidationError as exc:
        return [_error("INVALID_MODEL", f"データフローの形が正しくありません: {exc}")]
    function_list = FunctionListModel.model_validate(sources.stages.get(1) or {})
    function_ids = {row.id for row in function_list.functions}

    issues: list[StageIssue] = []
    if len(set(parsed.dfd_groups)) > MAX_DFD_GROUPS:
        message = f"DFD を描く機能グループは {MAX_DFD_GROUPS} つまでです。"
        issues.append(_error("TOO_MANY_DFD_GROUPS", message))
    for group, count in Counter(parsed.dfd_groups).items():
        if count > 1:
            message = f"DFD を描く機能グループ「{group}」が重複しています。"
            issues.append(_error("DUPLICATE_DFD_GROUP", message, group))
        if group not in function_list.groups:
            message = f"DFD を描く機能グループ「{group}」が、機能一覧にありません。"
            issues.append(_error("UNKNOWN_DFD_GROUP", message, group))

    summary_counts = Counter(row.function_id for row in parsed.summaries)
    for row in parsed.summaries:
        if row.function_id not in function_ids:
            message = f"処理概要表の {row.function_id} が、機能一覧にありません。"
            issues.append(_error("UNKNOWN_FUNCTION", message, row.function_id))
        elif not (row.input.strip() and row.process.strip() and row.output.strip()):
            message = f"{row.function_id} の処理概要(入力・処理内容・出力)に空の欄があります。"
            issues.append(_warning("EMPTY_SUMMARY", message, row.function_id))
    for function_id, count in summary_counts.items():
        if count > 1:
            message = f"処理概要表の {function_id} が重複しています。"
            issues.append(_error("DUPLICATE_SUMMARY", message, function_id))
    for function in function_list.functions:
        if function.id not in summary_counts:
            message = f"{function.id} が処理概要表にありません。"
            issues.append(_error("MISSING_SUMMARY", message, function.id))

    for group in dict.fromkeys(parsed.dfd_groups):
        issues.extend(_dfd_issues(group, function_list, sources))
    return issues


def _dfd_issues(
    group: str, function_list: FunctionListModel, sources: StageSources
) -> list[StageIssue]:
    """選んだ機能グループ1つ分の DFD の指摘。"""
    diagram = sources.dfd_diagrams.get(dfd_subject(group))
    if diagram is None:
        return [_error("DFD_MISSING", f"機能グループ「{group}」の DFD がまだありません。", group)]
    if diagram.generation_status == "generating":
        return [_error("DFD_GENERATING", f"機能グループ「{group}」の DFD を生成中です。", group)]
    issues: list[StageIssue] = []
    if diagram.status not in APPROVED_DIAGRAM_STATUSES:
        message = f"機能グループ「{group}」の DFD が承認されていません。"
        issues.append(_error("DFD_NOT_APPROVED", message, group))
    member_ids = [row.id for row in group_functions(function_list, group)]
    all_ids = {row.id for row in function_list.functions}
    for process_id in diagram.process_ids:
        if process_id in all_ids and process_id not in member_ids:
            message = (
                f"機能グループ「{group}」の DFD に、別のグループの処理 {process_id} があります。"
            )
            issues.append(_warning("DFD_FOREIGN_PROCESS", message, group))
    missing = [i for i in member_ids if i not in diagram.process_ids]
    if missing:
        message = f"機能グループ「{group}」の DFD に、{'・'.join(missing)} が描かれていません。"
        issues.append(_warning("DFD_MISSING_PROCESS", message, group))
    return issues


def _error(code: str, message: str, target: str | None = None) -> StageIssue:
    return StageIssue("error", code, message, target)


def _warning(code: str, message: str, target: str | None = None) -> StageIssue:
    return StageIssue("warning", code, message, target)


# 段階番号 → その段階の検証。登録の無い段階は検証なし(共通の承認条件だけ)。
STAGE_VALIDATORS: dict[int, StageValidator] = {
    1: validate_function_list,
    2: validate_data_flow,
}


def validate_stage(
    stage: int, model: Mapping[str, Any] | None, sources: StageSources
) -> list[StageIssue]:
    """段階の内容を検証する。内容が無い(未着手・生成中の初回)ときは指摘なし。"""
    validator = STAGE_VALIDATORS.get(stage)
    if validator is None or not model:
        return []
    return validator(model, sources)
