import uuid
from collections.abc import Sequence

from app.uml.domain.dfd import DfdDataStore, DfdElement, DfdExternalEntity, DfdFlow, DfdProcess
from app.uml.validation.base import ValidationIssue


def validate_dfd_rules(
    elements: Sequence[DfdElement],
    flows: Sequence[DfdFlow],
    data_item_ids: set[uuid.UUID],
    referenced_elsewhere: set[uuid.UUID] | frozenset[uuid.UUID] = frozenset(),
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """診断8(appendix/stage3-requirements-organization.md)のDFD規則を検証する。

    診断8の5点目「上位図と下位図の境界フローが一致する」は撤回した。Phase 10でDFDを
    「APIエンドポイント/バッチごとに1枚」のフラットな構成に確定し、上位図・下位図という
    階層自体を持たないため、判定の対象が存在しない。

    `data_item_ids`はプロジェクトのデータ辞書全件のID。`referenced_elsewhere`は、同じ
    プロジェクトの他のDFDが参照しているデータ項目のID。処理ごとに1枚の構成では、ある図が
    参照しないデータ項目の多くは他の図が参照しているため、「どこからも参照されない」は
    プロジェクト内の全DFDを横断して判定する(この図にも他の図にも無いものだけを警告する)。
    """
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    processes = {el.id for el in elements if isinstance(el, DfdProcess)}
    stores = {el.id for el in elements if isinstance(el, DfdDataStore)}
    entities = {el.id for el in elements if isinstance(el, DfdExternalEntity)}

    # 1. 全フローがデータ項目を持つ(参照先が実在するデータ項目か)
    for flow in flows:
        if flow.data_item_id not in data_item_ids:
            errors.append(
                ValidationIssue(
                    code="UNKNOWN_DATA_ITEM",
                    message=(
                        f"フロー{flow.id}が参照するデータ項目が見つかりません: {flow.data_item_id}"
                    ),
                    element_id=flow.id,
                )
            )

    # 2. 全処理に入力と出力が1つ以上ある
    inbound_count = dict.fromkeys(processes, 0)
    outbound_count = dict.fromkeys(processes, 0)
    for flow in flows:
        if flow.target_id in inbound_count:
            inbound_count[flow.target_id] += 1
        if flow.source_id in outbound_count:
            outbound_count[flow.source_id] += 1
    for pid in processes:
        if inbound_count[pid] == 0:
            errors.append(
                ValidationIssue(
                    code="PROCESS_MISSING_INPUT",
                    message=f"処理{pid}に入力フローがありません",
                    element_id=pid,
                )
            )
        if outbound_count[pid] == 0:
            errors.append(
                ValidationIssue(
                    code="PROCESS_MISSING_OUTPUT",
                    message=f"処理{pid}に出力フローがありません",
                    element_id=pid,
                )
            )

    # 3. ストア同士、外部実体とストアを直接つなぐフローがない(必ず処理を介す)
    for flow in flows:
        source_is_store, target_is_store = flow.source_id in stores, flow.target_id in stores
        source_is_entity, target_is_entity = flow.source_id in entities, flow.target_id in entities
        invalid_pair = (
            (source_is_store and target_is_store)
            or (source_is_store and target_is_entity)
            or (source_is_entity and target_is_store)
        )
        if invalid_pair:
            errors.append(
                ValidationIssue(
                    code="INVALID_DIRECT_FLOW",
                    message=f"フロー{flow.id}はストア/外部実体同士を処理を介さず直結しています",
                    element_id=flow.id,
                )
            )

    # 4. どこからも参照されないデータ項目がない(プロジェクト内の全DFDを横断し、警告として報告)
    referenced_item_ids = {flow.data_item_id for flow in flows} | set(referenced_elsewhere)
    for item_id in sorted(data_item_ids - referenced_item_ids, key=str):
        warnings.append(
            ValidationIssue(
                code="UNREFERENCED_DATA_ITEM",
                message=f"データ項目{item_id}はどのDFDのフローからも参照されていません",
                element_id=str(item_id),
            )
        )

    return errors, warnings
