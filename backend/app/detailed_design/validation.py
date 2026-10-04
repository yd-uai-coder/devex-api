"""段階ごとの内容の検証(純粋関数)。

承認の条件は、全段階に共通の3つ(段階が開いている・版が一致する・承認できる状態で内容が空でない。
app/services/design_stage_service.py)に加えて、段階ごとの検証で「エラー」が無いこと。警告は承認を
止めない(UML図の検証と同じ考え方。app/uml/validation/)。保存は検証の結果によらず通す(編集の
途中の状態も保存できるようにするため)。

段階ごとの検証は`STAGE_VALIDATORS`に登録する。今は段階1〜4で、段階5以降は各段階の Phase で
足す(登録の無い段階は検証なし)。検証には段階の内容のほかに入力の文書の本文が要ることがあるので、
`StageSources`で渡す(段階1は外部設計書のAPI一覧と照らして、下書きの漏れを警告する)。
段階2は、入力の段階1の内容と、機能グループの DFD(`uml_diagrams`)の要約も使う(Phase 17)。
段階3は、段階1・2の内容と、DFD の線から読み取った R/W と、ER の要約を使う(Phase 18)。
段階4は、段階1の内容と、構成図の要約を使う(Phase 19)。
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
from app.detailed_design.data_model import (
    CrudModel,
    DfdAccess,
    is_canonical_ops,
    table_key,
)
from app.detailed_design.function_list import FunctionListModel, function_number
from app.detailed_design.structure import ModuleListModel, module_ref_matches

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
    accesses: tuple[DfdAccess, ...] = ()


@dataclass(frozen=True)
class ErDiagramSummary:
    """段階3の検証に使う、ER(全体1枚)の要約(`uml_diagrams`の行から作る)。"""

    status: str
    generation_status: str
    tables: tuple[str, ...] = ()
    tables_without_pk: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComponentDiagramSummary:
    """段階4の検証に使う、構成図(全体1枚)の要約(`uml_diagrams`の行から作る)。"""

    status: str
    generation_status: str
    layers: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageSources:
    """検証・下書きの生成に使う入力。

    - `documents`: 入力の文書の本文(doc_type → 表示中の版の本文)。
    - `stages`: 入力の段階の内容(段階番号 → 承認済みの段階の`model`)。
    - `dfd_diagrams`: 機能グループの DFD の要約(subject → 要約)。段階2・3が使う。
    - `er_diagram`: ER の要約(まだ無ければ None)。段階3が使う。
    - `component_diagram`: 構成図の要約(まだ無ければ None)。段階4が使う。
    """

    documents: Mapping[str, str] = field(default_factory=dict)
    stages: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
    dfd_diagrams: Mapping[str, DfdDiagramSummary] = field(default_factory=dict)
    er_diagram: ErDiagramSummary | None = None
    component_diagram: ComponentDiagramSummary | None = None


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


def selected_dfd_accesses(sources: StageSources) -> list[DfdAccess]:
    """段階2で DFD を描くと選んだグループの DFD から読み取った R/W(段階3の CRUD 図の固定部分)。
    選択を外したグループの DFD は消さずに残っているので、選択で絞る。"""
    data_flow = DataFlowModel.model_validate(sources.stages.get(2) or {})
    accesses: set[DfdAccess] = set()
    for group in data_flow.dfd_groups:
        diagram = sources.dfd_diagrams.get(dfd_subject(group))
        if diagram is not None:
            accesses.update(diagram.accesses)
    return sorted(accesses)


def validate_data_model(model: Mapping[str, Any], sources: StageSources) -> list[StageIssue]:
    """段階3(データモデル)の検証。

    エラー: 形が不正 / ER が無い・生成中・未承認 / ER のテーブル名の重複 /
    セルの処理IDが機能一覧に無い / セルのテーブルが ER に無い / セルの重複 /
    操作が空・C,R,U,D の順の形でない /
    DFD に読みの線があるのに R が無い / DFD に書き込みの線があるのに C/U/D が無い。
    警告: 下書きのままのセルがある / DFD のデータストアが ER に無い /
    どの処理も触れないテーブル / 主キーの無いテーブル。
    """
    try:
        parsed = CrudModel.model_validate(model)
    except ValidationError as exc:
        return [_error("INVALID_MODEL", f"CRUD 図の形が正しくありません: {exc}")]
    function_list = FunctionListModel.model_validate(sources.stages.get(1) or {})
    function_ids = {row.id for row in function_list.functions}

    issues: list[StageIssue] = []
    er = sources.er_diagram
    if er is None:
        issues.append(_error("ER_MISSING", "ER がまだありません。"))
    elif er.generation_status == "generating":
        issues.append(_error("ER_GENERATING", "ER を生成中です。"))
    elif er.status not in APPROVED_DIAGRAM_STATUSES:
        issues.append(_error("ER_NOT_APPROVED", "ER が承認されていません。"))
    tables: dict[str, str] = {}
    for name in er.tables if er is not None else ():
        key = table_key(name)
        if key in tables:
            # テーブル名は CRUD 図のセルを引く鍵なので、重なるとどちらのテーブルのセルかが決まらない
            message = f"ER のテーブル名「{name}」が重複しています。テーブル名を変えてください。"
            issues.append(_error("DUPLICATE_TABLE", message, name))
        else:
            tables[key] = name

    ops_by_cell: dict[tuple[str, str], str] = {}
    for cell in parsed.cells:
        target = f"{cell.function_id} × {cell.table}"
        key = (cell.function_id, table_key(cell.table))
        if cell.function_id not in function_ids:
            message = f"CRUD 図の {cell.function_id} が、機能一覧にありません。"
            issues.append(_error("UNKNOWN_FUNCTION", message, target))
        if er is not None and key[1] not in tables:
            message = f"CRUD 図のテーブル「{cell.table}」が、ER にありません。"
            issues.append(_error("UNKNOWN_TABLE", message, target))
        if key in ops_by_cell:
            issues.append(_error("DUPLICATE_CELL", f"{target} のセルが重複しています。", target))
        elif cell.ops and not is_canonical_ops(cell.ops):
            message = (
                f"{target} の操作「{cell.ops}」は、C・R・U・D をこの順に並べた形ではありません。"
            )
            issues.append(_error("INVALID_OPS", message, target))
        ops_by_cell.setdefault(key, cell.ops)

    accesses = selected_dfd_accesses(sources)
    writes = {(a.function_id, a.table) for a in accesses if a.kind == "write"}
    store_not_in_er: list[str] = []
    for access in accesses:
        if access.table not in tables:
            if er is not None and access.table not in store_not_in_er:
                store_not_in_er.append(access.table)
            continue
        target = f"{access.function_id} × {tables[access.table]}"
        ops = ops_by_cell.get((access.function_id, access.table), "")
        if access.kind == "read" and "R" not in ops:
            message = f"DFD では {target} の読みの線がありますが、CRUD 図に R がありません。"
            issues.append(_error("DFD_READ_MISSING", message, target))
        if access.kind == "write" and not set(ops) & {"C", "U", "D"}:
            message = (
                f"DFD では {target} の書き込みの線がありますが、CRUD 図に C/U/D がありません"
                "(C・U・D のどれかを決めてください)。"
            )
            issues.append(_error("DFD_WRITE_MISSING", message, target))
    for (function_id, table), ops in ops_by_cell.items():
        # DFD の書き込みのセルが空なのは DFD_WRITE_MISSING で知らせるので、ここでは重ねない
        if not ops and (function_id, table) not in writes:
            target = f"{function_id} × {tables.get(table, table)}"
            issues.append(_error("EMPTY_OPS", f"{target} のセルに操作がありません。", target))

    drafts = sum(1 for cell in parsed.cells if cell.draft)
    if drafts:
        message = f"AI の下書きのままのセルが {drafts} 個あります(承認すると確定します)。"
        issues.append(_warning("DRAFT_CELLS", message))
    for table in store_not_in_er:
        message = f"DFD のデータストア「{table}」が、ER のテーブルにありません。"
        issues.append(_warning("STORE_NOT_IN_ER", message, table))
    used = {table for (_, table) in ops_by_cell}
    for key, name in tables.items():
        if key not in used:
            message = f"テーブル「{name}」を読み書きする処理がありません。"
            issues.append(_warning("UNUSED_TABLE", message, name))
    for name in er.tables_without_pk if er is not None else ():
        message = f"テーブル「{name}」に主キーがありません。"
        issues.append(_warning("TABLE_WITHOUT_PK", message, name))
    return issues


def validate_structure(model: Mapping[str, Any], sources: StageSources) -> list[StageIssue]:
    """段階4(ソフトウェア構造)の検証。

    エラー: 形が不正 / モジュールが0件 / 構成図が無い・生成中・未承認 /
    パスが空・重複 / 関わる処理の処理IDが機能一覧に無い。
    警告: 責務が空 / 層が構成図のレーンに無い / どのモジュールにも現れない処理 /
    依存先がパスの形(`/`を含む)なのに、当たるモジュールがモジュール一覧に無い(区切り単位の
    部分一致。`module_ref_matches`)。
    """
    try:
        parsed = ModuleListModel.model_validate(model)
    except ValidationError as exc:
        return [_error("INVALID_MODEL", f"モジュール一覧の形が正しくありません: {exc}")]
    function_list = FunctionListModel.model_validate(sources.stages.get(1) or {})
    function_ids = {row.id for row in function_list.functions}

    issues: list[StageIssue] = []
    if not parsed.modules:
        issues.append(_error("EMPTY_MODULES", "モジュールが1件もありません。"))
    component = sources.component_diagram
    if component is None:
        issues.append(_error("COMPONENT_MISSING", "構成図がまだありません。"))
    elif component.generation_status == "generating":
        issues.append(_error("COMPONENT_GENERATING", "構成図を生成中です。"))
    elif component.status not in APPROVED_DIAGRAM_STATUSES:
        issues.append(_error("COMPONENT_NOT_APPROVED", "構成図が承認されていません。"))

    path_counts = Counter(row.path.strip() for row in parsed.modules)
    paths = set(path_counts)
    layers = set(component.layers) if component is not None else set()
    covered: set[str] = set()
    for index, row in enumerate(parsed.modules, start=1):
        path = row.path.strip()
        if not path:
            issues.append(_error("EMPTY_PATH", f"{index}行目のモジュールのパスが空です。"))
            continue
        if not row.responsibility.strip():
            issues.append(_warning("EMPTY_RESPONSIBILITY", f"{path} の責務が空です。", path))
        if component is not None and row.layer.strip() not in layers:
            message = f"{path} の層「{row.layer}」が、構成図の層にありません。"
            issues.append(_warning("UNKNOWN_LAYER", message, path))
        for function_id in row.functions:
            if function_id not in function_ids:
                message = f"{path} の関わる処理 {function_id} が、機能一覧にありません。"
                issues.append(_error("UNKNOWN_FUNCTION", message, path))
        if not row.all_functions:
            # 横断のモジュール(全処理)はカバーに数えない(どの処理の担当かが分からないため)
            covered.update(row.functions)
        for dependency in row.depends_on:
            # 依存先はディレクトリや短い書き方でもよい(区切り単位の部分一致。module_ref_matches)
            if "/" in dependency and not any(module_ref_matches(dependency, p) for p in paths):
                message = (
                    f"{path} の依存先「{dependency}」に当たるモジュールが、"
                    "モジュール一覧にありません。"
                )
                issues.append(_warning("UNKNOWN_DEPENDENCY", message, path))
    for path, count in path_counts.items():
        if path and count > 1:
            # パスは段階5の関与表の列の鍵なので、重なるとどちらのモジュールかが決まらない
            issues.append(_error("DUPLICATE_PATH", f"モジュール {path} が重複しています。", path))
    for function in function_list.functions:
        if function.id not in covered:
            message = f"{function.id} に関わるモジュールがありません(全処理の行は数えません)。"
            issues.append(_warning("UNCOVERED_FUNCTION", message, function.id))
    return issues


def _error(code: str, message: str, target: str | None = None) -> StageIssue:
    return StageIssue("error", code, message, target)


def _warning(code: str, message: str, target: str | None = None) -> StageIssue:
    return StageIssue("warning", code, message, target)


# 段階番号 → その段階の検証。登録の無い段階は検証なし(共通の承認条件だけ)。
STAGE_VALIDATORS: dict[int, StageValidator] = {
    1: validate_function_list,
    2: validate_data_flow,
    3: validate_data_model,
    4: validate_structure,
}


def validate_stage(
    stage: int, model: Mapping[str, Any] | None, sources: StageSources
) -> list[StageIssue]:
    """段階の内容を検証する。内容が無い(未着手・生成中の初回)ときは指摘なし。"""
    validator = STAGE_VALIDATORS.get(stage)
    if validator is None or not model:
        return []
    return validator(model, sources)
