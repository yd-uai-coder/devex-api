"""段階ごとの内容の検証(純粋関数)。

承認の条件は、全段階に共通の3つ(段階が開いている・版が一致する・承認できる状態で内容が空でない。
app/services/design_stage_service.py)に加えて、段階ごとの検証で「エラー」が無いこと。警告は承認を
止めない(UML図の検証と同じ考え方。app/uml/validation/)。保存は検証の結果によらず通す(編集の
途中の状態も保存できるようにするため)。

段階ごとの検証は`STAGE_VALIDATORS`に登録する。段階1〜8のすべてを登録している(登録の無い
段階は検証なし)。
検証には段階の内容のほかに入力の文書の本文が要ることがあるので、
`StageSources`で渡す(段階1は外部設計書のAPI一覧と照らして、下書きの漏れを警告する)。
段階2は、入力の段階1の内容と、機能グループの DFD(`uml_diagrams`)の要約も使う。
段階3は、段階1・2の内容と、DFD の線から読み取った R/W と、ER の要約を使う。
段階4は、段階1の内容と、構成図の要約を使う。
段階5は、段階1の内容と、段階4のモジュール一覧(手順の呼び出し先の鍵)を使う。
段階6は、段階5の手順(関数を呼ぶ手順との紐づけ)を使う。
段階7は、段階1の処理ID(計画の漏れ)と、段階4のモジュール一覧(単位のモジュールの欄)を使う。
環境・設定のファイルの欄と、横断事項のファイルの欄は例なので検証しない。
段階8は、段階7の作業単位と、単位が参照する段階3〜6の設計を使う(実装可能性チェック)。
設計の不足は、重要度(`level`)と直す先の段階(`fix_stage`)を持つ警告にする。段階8の指摘は
手順書がまだ無くても出る(段階7の単位と設計から決まるため)。
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
from app.detailed_design.logic import (
    LogicModel,
    is_drafted,
    logic_candidates,
    logic_id,
    logic_key,
)
from app.detailed_design.plan import (
    MAX_UNIT_FUNCTIONS,
    PLAN_STAGE,
    PlanModel,
    is_file_path,
    milestone_id,
    missing_topics,
    task_id,
    unit_ids,
    unplanned_functions,
)
from app.detailed_design.procedure import (
    ProcedureModel,
    is_external_actor,
    number_steps,
    step_id,
)
from app.detailed_design.procedure_doc import (
    PROCEDURE_DOC_STAGE,
    DesignIndex,
    FindingLevel,
    PlanUnit,
    ProcedureDocModel,
    design_index,
    plan_units,
    spelling_match,
    unit_refs,
)
from app.detailed_design.structure import ModuleListModel, module_ref_matches

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class StageIssue:
    """検証の指摘1件。`target`は指摘の対象(処理ID・機能グループ名など。無ければNone)。

    段階8(実装可能性チェック)の指摘だけが、重要度`level`・直す先の段階`fix_stage`・
    指摘の出た作業単位`unit`(単位によらなければNone)を持つ。"""

    severity: Severity
    code: str
    message: str
    target: str | None = None
    level: FindingLevel | None = None
    fix_stage: int | None = None
    unit: str | None = None


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
    警告: DFD を描く機能グループが1つも無い / 処理概要表の入力・処理内容・出力が空 /
    DFD にそのグループでない処理がある / DFD に描かれていないグループの処理がある。
    """
    try:
        parsed = DataFlowModel.model_validate(model)
    except ValidationError as exc:
        return [_error("INVALID_MODEL", f"データフローの形が正しくありません: {exc}")]
    function_list = FunctionListModel.model_validate(sources.stages.get(1) or {})
    function_ids = {row.id for row in function_list.functions}

    issues: list[StageIssue] = []
    if not parsed.dfd_groups:
        # 段階3の ER は DFD のデータストアとデータ辞書から下書きするので、DFD が無いと空になる
        message = (
            "DFD を描く機能グループが選ばれていません。段階3の ER は DFD のデータストアと"
            "データ辞書から下書きするため、テーブルが作られません。"
        )
        issues.append(_warning("NO_DFD_GROUPS", message))
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

    エラー: 形が不正 / ER が無い・生成中・未承認 / ER にテーブルが無い / ER のテーブル名の重複 /
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
    if er is not None and er.generation_status != "generating" and not er.tables:
        # テーブルの無い ER は、段階4の構成図・モジュール一覧と段階5の手順の入力にならない
        message = (
            "ER にテーブルがありません。段階2で DFD を描く機能グループを選んでから、"
            "作り直してください。"
        )
        issues.append(_error("ER_EMPTY", message))
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


def validate_procedures(model: Mapping[str, Any], sources: StageSources) -> list[StageIssue]:
    """段階5(主要処理の手順)の検証。

    エラー: 形が不正 / 処理が0件 / 処理IDが機能一覧に無い・重複 / 手順が0件 /
    先頭の行が分岐 / 手順の呼び出し先が空 / パスの形(`/`を含む)の呼び出し先が、モジュール一覧の
    パスと完全一致しない(関与表の列の鍵のため)。
    警告: 選定理由が空 / モジュールを呼ぶ手順の関数が空(段階6で関数を選べないため)。
    """
    try:
        parsed = ProcedureModel.model_validate(model)
    except ValidationError as exc:
        return [_error("INVALID_MODEL", f"手順の形が正しくありません: {exc}")]
    function_list = FunctionListModel.model_validate(sources.stages.get(1) or {})
    function_ids = {row.id for row in function_list.functions}
    modules = ModuleListModel.model_validate(sources.stages.get(4) or {})
    paths = {row.path.strip() for row in modules.modules}

    issues: list[StageIssue] = []
    if not parsed.procedures:
        issues.append(_error("EMPTY_PROCEDURES", "手順を書く処理が1つも選ばれていません。"))
    counts = Counter(procedure.function_id for procedure in parsed.procedures)
    for function_id, count in counts.items():
        if count > 1:
            message = f"{function_id} が2回選ばれています。"
            issues.append(_error("DUPLICATE_PROCEDURE", message, function_id))
    for procedure in parsed.procedures:
        function_id = procedure.function_id
        if function_id not in function_ids:
            message = f"処理 {function_id} が、機能一覧にありません。"
            issues.append(_error("UNKNOWN_FUNCTION", message, function_id))
        if not procedure.reason.strip():
            message = f"{function_id} の選定理由が空です。"
            issues.append(_warning("EMPTY_REASON", message, function_id))
        if not procedure.steps:
            message = f"{function_id} の手順がありません(下書きを生成するか、行を足してください)。"
            issues.append(_error("EMPTY_STEPS", message, function_id))
            continue
        if procedure.steps[0].is_branch:
            message = f"{function_id} の先頭の行が分岐です(分岐は元の手順の直後に置きます)。"
            issues.append(_error("LEADING_BRANCH", message, function_id))
        for step, number in zip(procedure.steps, number_steps(procedure.steps), strict=True):
            if step.is_branch:
                continue
            target = step_id(function_id, number)
            callee = step.callee.strip()
            if not callee:
                message = f"手順 {target} の呼び出し先が空です。"
                issues.append(_error("EMPTY_CALLEE", message, target))
            elif not is_external_actor(callee) and callee not in paths:
                # 関与表の列はモジュール一覧のパスなので、当たらない呼び出し先は表から漏れる
                message = (
                    f"手順 {target} の呼び出し先「{callee}」が、モジュール一覧のパスにありません。"
                )
                issues.append(_error("UNKNOWN_CALLEE", message, target))
            elif not is_external_actor(callee) and not step.call.strip():
                message = f"手順 {target} の呼ぶ関数が空です(段階6で関数を選べません)。"
                issues.append(_warning("EMPTY_CALL", message, target))
    return issues


def validate_logics(model: Mapping[str, Any], sources: StageSources) -> list[StageIssue]:
    """段階6(処理ロジックの詳細)の検証。0件はエラーにしない(段階6を飛ばす操作)。

    エラー: 形が不正 / モジュール・関数が空 / (モジュール, 関数)の重複 / 段階5のどの手順からも
    呼ばれない(06 の「呼ばれる手順」が空になるため) / 下書きが無い(シグネチャと擬似フローが空)。
    警告: 事前条件・事後条件が空。指摘の`target`は L-ID。
    """
    try:
        parsed = LogicModel.model_validate(model)
    except ValidationError as exc:
        return [_error("INVALID_MODEL", f"処理ロジックの形が正しくありません: {exc}")]
    procedures = ProcedureModel.model_validate(sources.stages.get(5) or {})
    called = {logic_key(c.module, c.function) for c in logic_candidates(procedures)}

    issues: list[StageIssue] = []
    counts = Counter(logic_key(row.module, row.function) for row in parsed.logics)
    for index, row in enumerate(parsed.logics):
        target = logic_id(index)
        label = f"{target}({row.module.strip()} の {row.function.strip()})"
        if not row.module.strip() or not row.function.strip():
            message = f"{target} のモジュールか関数が空です。"
            issues.append(_error("EMPTY_LOGIC_KEY", message, target))
            continue
        key = logic_key(row.module, row.function)
        if counts[key] > 1:
            message = f"{label} が2回選ばれています。"
            issues.append(_error("DUPLICATE_LOGIC", message, target))
        if key not in called:
            # 段階5の手順を直して呼ばれなくなった関数は、05 から辿れない(バッジが付かない)
            message = f"{label} を呼ぶ手順が、段階5にありません。"
            issues.append(_error("UNCALLED_LOGIC", message, target))
        if not is_drafted(row):
            message = f"{label} の詳細がありません(下書きを生成するか、記入してください)。"
            issues.append(_error("EMPTY_LOGIC", message, target))
            continue
        if not row.pre.strip() or not row.post.strip():
            message = f"{label} の事前条件か事後条件が空です。"
            issues.append(_warning("EMPTY_CONDITION", message, target))
    return issues


def validate_plan(model: Mapping[str, Any], sources: StageSources) -> list[StageIssue]:
    """段階7(横断事項と実装計画)の検証。

    エラー: 形が不正 / マイルストーンが0件 / マイルストーン名が空・重複 / タスク名が空 /
    横断事項の項目が空 / 機能一覧に無い処理ID / 単位の一覧に無い依存先 / 自分か後ろの単位への
    依存(前の単位だけを指せるので、循環も起きない) / 段階4のモジュール一覧に無いモジュール
    (単位の参照先の鍵のため)。
    環境・設定のファイルの欄と横断事項のファイルの欄は例なので検証しない(環境のファイルは
    モジュール一覧に入らないため)。
    警告: どの単位にも無い処理 / 複数の単位にある処理 / タスクの無いマイルストーン /
    種別と処理の食い違い(機能なのに処理が無い・基盤なのに処理がある) / 処理が多すぎる単位 /
    モジュールの無い機能の単位 / ディレクトリのモジュール(ファイルが決まらない) /
    横断事項の既定の項目(`CROSSCUTTING_TOPICS`)が無い・方針が空 / リスクが0件。
    指摘の`target`は、マイルストーンは M-ID、単位は単位の ID、横断事項は項目名、漏れた処理は処理ID。
    """
    try:
        parsed = PlanModel.model_validate(model)
    except ValidationError as exc:
        return [_error("INVALID_MODEL", f"横断事項と実装計画の形が正しくありません: {exc}")]
    function_list = FunctionListModel.model_validate(sources.stages.get(1) or {})
    function_ids = [row.id for row in function_list.functions]
    known_functions = set(function_ids)
    module_list = ModuleListModel.model_validate(sources.stages.get(4) or {})
    paths = {row.path.strip() for row in module_list.modules}
    order = {uid: index for index, uid in enumerate(unit_ids(parsed))}

    issues: list[StageIssue] = []

    for index, row in enumerate(parsed.crosscutting, start=1):
        topic = row.topic.strip()
        if not topic:
            message = f"横断事項の{index}行目の項目が空です。"
            issues.append(_error("EMPTY_TOPIC", message))
            continue
        if not row.policy.strip():
            issues.append(_warning("EMPTY_POLICY", f"横断事項「{topic}」の方針が空です。", topic))
    for topic in missing_topics(parsed):
        message = f"横断事項に「{topic}」がありません。"
        issues.append(_warning("MISSING_TOPIC", message, topic))

    if not parsed.milestones:
        issues.append(_error("NO_MILESTONE", "マイルストーンが1件もありません。"))
    names = Counter(m.name.strip() for m in parsed.milestones)
    units_of: dict[str, list[str]] = {}
    for m_index, milestone in enumerate(parsed.milestones):
        target = milestone_id(m_index)
        name = milestone.name.strip()
        label = f"{target}({name})" if name else target
        if not name:
            issues.append(_error("EMPTY_MILESTONE_NAME", f"{target} の名前が空です。", target))
        elif names[name] > 1:
            message = f"マイルストーン「{name}」が重複しています。"
            issues.append(_error("DUPLICATE_MILESTONE", message, target))
        if not milestone.tasks:
            issues.append(_warning("EMPTY_TASKS", f"{label} にタスクがありません。", target))
        for t_index, task in enumerate(milestone.tasks):
            uid = task_id(m_index, t_index)
            functions = [f.strip() for f in task.function_ids if f.strip()]
            if not task.title.strip():
                issues.append(_error("EMPTY_TASK", f"{uid} の名前が空です。", uid))
            for function_id in functions:
                units_of.setdefault(function_id, []).append(uid)
                if function_id not in known_functions:
                    message = f"{uid} の処理 {function_id} が、機能一覧にありません。"
                    issues.append(_error("UNKNOWN_FUNCTION", message, uid))
            issues += _task_kind_issues(uid, task.kind, functions, task.modules)
            issues += _dependency_issues(uid, task.depends_on, order)
            issues += _module_issues(uid, task.modules, paths)

    for function_id, units in units_of.items():
        if len(units) > 1 and function_id in known_functions:
            message = f"{function_id} が、複数の単位({', '.join(units)})にあります。"
            issues.append(_warning("DUPLICATE_FUNCTION", message, function_id))
    for function_id in unplanned_functions(parsed, function_ids):
        message = f"{function_id} が、どの単位にもありません。"
        issues.append(_warning("UNPLANNED_FUNCTION", message, function_id))
    if not parsed.risks:
        issues.append(_warning("NO_RISKS", "想定リスクが1件もありません。"))
    return issues


def _task_kind_issues(
    uid: str, kind: str, functions: list[str], modules: list[str]
) -> list[StageIssue]:
    """単位の種別と、処理・モジュールの食い違い(警告)。"""
    issues: list[StageIssue] = []
    if kind == "feature":
        if not functions:
            message = f"{uid} は機能の単位ですが、処理がありません(処理の無い作業は基盤にします)。"
            issues.append(_warning("KIND_MISMATCH", message, uid))
        elif len(functions) > MAX_UNIT_FUNCTIONS:
            message = (
                f"{uid} に処理が{len(functions)}個あります(1つの単位は原則1処理です。"
                "分けられないか確かめてください)。"
            )
            issues.append(_warning("MANY_FUNCTIONS", message, uid))
        if not any(m.strip() for m in modules):
            message = f"{uid} にモジュールがありません(作る・直すファイルが決まりません)。"
            issues.append(_warning("NO_MODULES", message, uid))
    elif functions:
        message = f"{uid} は基盤の単位ですが、処理があります(処理を持つ作業は機能にします)。"
        issues.append(_warning("KIND_MISMATCH", message, uid))
    return issues


def _dependency_issues(
    uid: str, depends_on: list[str], order: Mapping[str, int]
) -> list[StageIssue]:
    """依存先が単位の一覧に無い・自分か後ろの単位を指す(エラー)。"""
    issues: list[StageIssue] = []
    for dependency in (d.strip() for d in depends_on if d.strip()):
        if dependency not in order:
            message = f"{uid} の依存先 {dependency} が、単位の一覧にありません。"
            issues.append(_error("UNKNOWN_DEPENDENCY", message, uid))
        elif order[dependency] >= order[uid]:
            # 前の単位だけを指せるようにすると、依存の順と計画の並び順が一致し、循環も起きない
            message = (
                f"{uid} の依存先 {dependency} が、自分か後ろの単位です"
                "(依存先は前に並べます)。"
            )
            issues.append(_error("FORWARD_DEPENDENCY", message, uid))
    return issues


def _module_issues(uid: str, modules: list[str], paths: set[str]) -> list[StageIssue]:
    """モジュールが段階4のモジュール一覧に無い(エラー)・ディレクトリ(警告)。"""
    issues: list[StageIssue] = []
    for module in (m.strip() for m in modules if m.strip()):
        if module not in paths:
            # 環境・設定のファイルは別の欄に書く(モジュールの欄は手順書が参照する設計の鍵)
            message = (
                f"{uid} のモジュール「{module}」が、モジュール一覧にありません"
                "(環境・設定のファイルなら、その欄に移します)。"
            )
            issues.append(_error("UNKNOWN_MODULE", message, uid))
        elif not is_file_path(module):
            message = (
                f"{uid} のモジュール「{module}」はディレクトリで、ファイルが決まりません"
                "(段階4のモジュール一覧をファイル単位に直します)。"
            )
            issues.append(_warning("MODULE_NOT_FILE", message, uid))
    return issues


def validate_procedure_doc(
    model: Mapping[str, Any], sources: StageSources
) -> list[StageIssue]:
    """段階8(実装手順書)の検証 = 決定的な実装可能性チェック。

    エラー(承認を止める): 形が不正 / 同じ単位の手順書が2つある / 手順書の単位が段階7に無い・
    タスク名が違う(段階7を並べ替え・改名した。作り直させる)。
    警告(重要度・直す先の段階を持つ。承認は止めない):
    - 中程度・段階5: 機能の単位の処理に、段階5の手順が無い(`NO_PROCEDURE`)
    - 中程度・段階3: 手順に DB 操作があるのに、CRUD 図にその処理の操作が無い(`NOT_IN_CRUD`)
    - 中程度・段階4: 単位のモジュールがディレクトリ(`MODULE_NOT_FILE`)、手順書のモジュールの
      ファイルがモジュール一覧に無い(`UNKNOWN_FILE`)
    - 軽微・段階5: 手順の呼ぶ関数が段階6に無く、同じモジュールの段階6の関数と書き方だけが違う
      (`UNRESOLVED_CALL`。揺れは吸収せず、手順の関数名を段階6にそろえさせる。段階6で選ばなかった
      別の関数は、段階6が任意なので指摘しない)
    指摘の`target`は、処理ID・手順ID・パス・単位の ID。`unit`は指摘の出た単位の ID。
    """
    try:
        parsed = ProcedureDocModel.model_validate(model)
    except ValidationError as exc:
        return [_error("INVALID_MODEL", f"実装手順書の形が正しくありません: {exc}")]
    units = plan_units(PlanModel.model_validate(sources.stages.get(PLAN_STAGE) or {}))
    by_id = {unit.unit_id: unit for unit in units}
    index = design_index(sources.stages)

    issues: list[StageIssue] = []
    counts = Counter(doc.unit_id.strip() for doc in parsed.units)
    reported: set[str] = set()
    for doc in parsed.units:
        uid = doc.unit_id.strip()
        if counts[uid] > 1:
            if uid not in reported:
                reported.add(uid)
                message = f"{uid} の手順書が{counts[uid]}つあります。"
                issues.append(_unit_error("DUPLICATE_UNIT", message, uid))
            continue
        unit = by_id.get(uid)
        if unit is None:
            message = f"手順書の単位 {uid} が、段階7にありません(手順書を作り直してください)。"
            issues.append(_unit_error("UNIT_MISMATCH", message, uid))
            continue
        if unit.task.title.strip() != doc.title.strip():
            message = (
                f"{uid} の手順書は「{doc.title.strip()}」のものですが、段階7の {uid} は"
                f"「{unit.task.title.strip()}」です(段階7を並べ替えたか改名しました。"
                "手順書を作り直してください)。"
            )
            issues.append(_unit_error("UNIT_MISMATCH", message, uid))
            continue
        for file in doc.files:
            path = file.path.strip()
            if file.kind == "module" and path and path not in index.module_paths:
                message = f"{uid} の手順書のファイル「{path}」が、モジュール一覧にありません。"
                issues.append(_finding("UNKNOWN_FILE", message, path, "major", 4, uid))
    for unit in units:
        issues += _unit_design_issues(unit, index)
    return issues


def _unit_design_issues(unit: PlanUnit, index: DesignIndex) -> list[StageIssue]:
    """単位1つの、参照する設計の不足(警告)。基盤の単位は処理を持たないので、モジュールだけを見る。"""
    uid = unit.unit_id
    issues: list[StageIssue] = []
    for ref in unit_refs(unit.task, index):
        if ref.kind == "procedure":
            if not ref.resolved:
                message = f"{uid} の処理 {ref.key} に、段階5の手順がありません。"
                issues.append(_finding("NO_PROCEDURE", message, ref.key, "major", 5, uid))
            elif _has_db_step(index, ref.key) and ref.key not in index.crud_functions:
                message = (
                    f"{uid} の処理 {ref.key} の手順に DB 操作がありますが、"
                    "CRUD 図にこの処理の操作がありません。"
                )
                issues.append(_finding("NOT_IN_CRUD", message, ref.key, "major", 3, uid))
        elif ref.kind == "logic":
            module, function = ref.key.split("::", 1)
            name = None if ref.resolved else spelling_match(index, module, function)
            if name is not None:
                message = (
                    f"{uid} の手順 {ref.via} の関数「{function}」は、段階6の「{name}」と"
                    f"書き方だけが違います。段階5の手順の関数名を「{name}」にそろえます。"
                )
                issues.append(_finding("UNRESOLVED_CALL", message, ref.via, "minor", 5, uid))
        elif ref.resolved and not is_file_path(ref.key):
            message = (
                f"{uid} のモジュール「{ref.key}」はディレクトリで、作るファイルが決まりません。"
            )
            issues.append(_finding("MODULE_NOT_FILE", message, ref.key, "major", 4, uid))
    return issues


def _has_db_step(index: DesignIndex, function_id: str) -> bool:
    procedure = index.procedures.get(function_id)
    return procedure is not None and any(step.db.strip() for step in procedure.steps)


def _unit_error(code: str, message: str, uid: str) -> StageIssue:
    """段階8の手順書そのもののエラー(手順書を作り直すので、直す先は段階8)。"""
    return StageIssue("error", code, message, uid, fix_stage=PROCEDURE_DOC_STAGE, unit=uid)


def _finding(
    code: str,
    message: str,
    target: str | None,
    level: FindingLevel,
    fix_stage: int,
    unit: str | None,
) -> StageIssue:
    """段階8の実装可能性チェックの警告(重要度と直す先の段階を持つ)。"""
    return StageIssue("warning", code, message, target, level, fix_stage, unit)


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
    5: validate_procedures,
    6: validate_logics,
    7: validate_plan,
    8: validate_procedure_doc,
}

# 内容が無くても検証する段階。段階8の指摘は、手順書の無い単位にも段階7と設計から出るため
VALIDATED_WITHOUT_MODEL: frozenset[int] = frozenset({PROCEDURE_DOC_STAGE})


def validate_stage(
    stage: int, model: Mapping[str, Any] | None, sources: StageSources
) -> list[StageIssue]:
    """段階の内容を検証する。内容が無い(未着手・生成中の初回)ときは指摘なし。
    ただし`VALIDATED_WITHOUT_MODEL`の段階は、空の内容として検証する。"""
    validator = STAGE_VALIDATORS.get(stage)
    if validator is None:
        return []
    if not model:
        if stage not in VALIDATED_WITHOUT_MODEL:
            return []
        model = {}
    return validator(model, sources)
