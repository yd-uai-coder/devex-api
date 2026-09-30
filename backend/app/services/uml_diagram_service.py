import asyncio
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.models.uml_diagram import UmlDiagram
from app.repositories.data_item import DataItemRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.errors import (
    LayoutNodeLimitExceededError,
    LayoutValidationFailedError,
    UmlApprovalValidationFailedError,
    UmlDiagramNotApprovableError,
    UmlDiagramNotApprovedError,
    UmlDiagramNotFoundError,
    UmlDiagramVersionConflictError,
    UmlGenerationInProgressError,
    UmlLayoutRequiredError,
)
from app.uml.domain import (
    STATUS_AFTER_APPROVE,
    STATUS_AFTER_EDIT,
    STATUS_AFTER_EXPORT,
    ComponentSemanticModel,
    DfdSemanticModel,
    ErSemanticModel,
    NotationType,
    SemanticModelAdapter,
    can_approve,
    can_export,
    parse_status,
)
from app.uml.export import build_render, to_drawio, to_svg
from app.uml.layout import LayoutModel, edge_labels, reconcile_layout
from app.uml.layout import compute_layout as compute_layout_engine
from app.uml.validation import ValidationResult, validate_diagram
from app.uml.validation.structural import MAX_ELEMENTS

ExportFormat = Literal["drawio", "svg"]

_MEDIA_TYPES: dict[ExportFormat, str] = {"drawio": "application/xml", "svg": "image/svg+xml"}

# 出力するファイルの題名(devex-ui labels.tsのNOTATION_LABELS/diagramTitleと同じ文言)
_NOTATION_TITLES: dict[NotationType, str] = {
    "component": "コンポーネント図",
    "er": "ER図",
    "dfd": "データフロー図",
}

# ファイル名に使えない文字(Windowsを含む主要OSの禁止文字と制御文字)
_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


@dataclass(frozen=True)
class ExportedFile:
    """出力したファイル1つ分(ルートがそのままダウンロードのレスポンスにする)。"""

    filename: str
    content: str
    media_type: str


class UmlDiagramService:
    """UML設計図(component/er/dfd)に対するユースケース(一覧・取得・更新・検証・レイアウト・承認)を
    担当するサービス。AI生成(M1)はapp/services/uml_generation_service.pyが担う
    """

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._session = session
        self._diagrams = UmlDiagramRepository(session)
        self._data_items = DataItemRepository(session)

    async def list_for_project(self, project_id: uuid.UUID) -> list[UmlDiagram]:
        """指定プロジェクトのUML図一覧を取得する。"""
        return await self._diagrams.list_for_project(project_id)

    async def get(self, *, project_id: uuid.UUID, diagram_id: uuid.UUID) -> UmlDiagram:
        """UML図を1件取得する(他プロジェクトのものは404扱い)。"""
        return await self._get_owned(project_id=project_id, diagram_id=diagram_id)

    async def update(
        self,
        *,
        project_id: uuid.UUID,
        diagram_id: uuid.UUID,
        expected_version: int,
        semantic_model: ComponentSemanticModel | ErSemanticModel | DfdSemanticModel,
        layout_model: LayoutModel | None = None,
    ) -> UmlDiagram:
        """UML図の意味モデル全体を更新する(楽観ロック)。

        `layout_model`を渡した場合は、レビュー画面で手動移動した配置として意味モデルと同じ
        versionで保存する。省略した場合は保存済みの配置を保つ。どちらの場合も、新しい意味モデルに
        存在しない要素・関係のジオメトリは`reconcile_layout`で落とす(要素の削除を1回の保存で
        反映するため)。

        `expected_version`がDB上の現在のversionと一致しない場合、他の更新と競合している
        とみなしUmlDiagramVersionConflictError(409)にする(devex-api既存コードベースに前例の
        無い新規パターン。generated_documents.versionは追記のたびに増える版数であり、
        書き込み競合検知の仕組みではない)。
        """
        diagram = await self._get_owned(project_id=project_id, diagram_id=diagram_id)
        _ensure_not_generating(diagram)
        if semantic_model.notation != diagram.notation:
            raise BadRequestError(
                f"notation は変更できません(現在: {diagram.notation}, "
                f"指定値: {semantic_model.notation})"
            )
        _ensure_version(diagram, expected_version)
        diagram.semantic_model = semantic_model.model_dump(mode="json")
        base_layout = (
            layout_model
            if layout_model is not None
            else (
                LayoutModel.model_validate(diagram.layout_model)
                if diagram.layout_model is not None
                else None
            )
        )
        if base_layout is not None:
            diagram.layout_model = reconcile_layout(base_layout, semantic_model).model_dump(
                mode="json"
            )
        # 承認済みの図を保存したら承認をやり直す(M7。座標だけの保存も含む)
        diagram.status = STATUS_AFTER_EDIT
        diagram.version += 1
        await self._session.flush()
        await self._session.commit()
        # updated_at は server-side の onupdate=func.now() で決まるため、UPDATE後は
        # DBが計算した値を明示的に取り直す(取らないと後続のPydantic変換時に
        # 「コミット後に未ロードの属性へ同期アクセスした」MissingGreenletで落ちる)。
        await self._session.refresh(diagram)
        return diagram

    async def validate(self, *, project_id: uuid.UUID, diagram_id: uuid.UUID) -> ValidationResult:
        """UML図を検証する。DBのsemantic_model(生dict)を型付きモデルへ復元してから
        app.uml.validation.validate_diagramへ渡す。DFDの場合はプロジェクトのデータ辞書全件の
        idと、他のDFDが参照しているデータ項目のidも渡す(未参照データ項目の判定を全DFDで横断する)。"""
        diagram = await self._get_owned(project_id=project_id, diagram_id=diagram_id)
        model = SemanticModelAdapter.validate_python(diagram.semantic_model)
        return await self._validate_model(diagram, model)

    async def compute_layout(self, *, project_id: uuid.UUID, diagram_id: uuid.UUID) -> UmlDiagram:
        """UML図の自動レイアウト(M6)を実行し、`layout_model`を保存する。

        レイアウトエンジン(`app.uml.layout`)を実行する前に2つの事前チェックを行う
        (Phase-7-4.md「Phase 9への申し送り」#1・#2): (1) 要素数が`MAX_ELEMENTS`
        (`app.uml.validation.structural`と共有する上限、目安30)を超える場合は
        `LayoutNodeLimitExceededError`(診断3「上限超過の検証エラー化」)。(2) M4構造検証
        (ID重複・参照切れ等)を通らない場合は`LayoutValidationFailedError`
        ── 重複ID・未定義ノード参照は、レイアウトエンジン内でassertせず
        この事前検証に一本化する(レイアウトエンジン単体はもう防御しない)。

        レイアウト計算自体はCPU負荷が高い(経路探索・交差削減の山登りでO(n²)〜O(n!))ため、
        `asyncio.to_thread`でイベントループをブロックしないようにする
        (devex-api既存コードベースに前例の無い新規パターン)。
        """
        diagram = await self._get_owned(project_id=project_id, diagram_id=diagram_id)
        _ensure_not_generating(diagram)
        model = SemanticModelAdapter.validate_python(diagram.semantic_model)

        if len(model.elements) > MAX_ELEMENTS:
            raise LayoutNodeLimitExceededError(
                f"要素数が上限({MAX_ELEMENTS})を超えています: {len(model.elements)}件"
            )

        validation_result = await self._validate_model(diagram, model)
        if not validation_result.is_valid:
            messages = "; ".join(issue.message for issue in validation_result.errors)
            raise LayoutValidationFailedError(
                f"意味モデルの検証エラーのためレイアウトを計算できません: {messages}"
            )

        label_texts = edge_labels(model, await self._data_item_names(diagram, model))
        layout_model = await asyncio.to_thread(
            compute_layout_engine, str(diagram.id), model, label_texts
        )
        diagram.layout_model = layout_model.model_dump(mode="json")
        # 配置が変わると出力の見た目も変わるため、保存と同じく承認をやり直す(M7)
        diagram.status = STATUS_AFTER_EDIT
        await self._session.flush()
        await self._session.commit()
        # updated_at は server-side の onupdate=func.now() で決まるため、UPDATE後は
        # DBが計算した値を明示的に取り直す(update()と同じ理由)。
        await self._session.refresh(diagram)
        return diagram

    async def approve(
        self, *, project_id: uuid.UUID, diagram_id: uuid.UUID, expected_version: int
    ) -> UmlDiagram:
        """UML図を承認する(M7: draft / reviewing → approved)。

        `expected_version`は、利用者が画面で見ていた版。承認は「その内容」に対するものなので、
        見ていない版を承認しないよう、保存と同じく楽観ロックで確かめる。状態が変わっても
        `version`は増やさない(`version`は内容の楽観ロック専用。app/uml/domain/status.py参照)。

        承認の条件(この順に確かめる):
        1. 生成中でない
        2. versionが一致する
        3. 承認できる状態(draft / reviewing)である
        4. 配置があり、全要素の配置を含む(出力は座標が無いと描けないため)
        5. 検証(M4)にエラーが無い(警告は承認を妨げない)
        """
        diagram = await self._get_owned(project_id=project_id, diagram_id=diagram_id)
        _ensure_not_generating(diagram)
        _ensure_version(diagram, expected_version)
        if not can_approve(parse_status(diagram.status)):
            raise UmlDiagramNotApprovableError(
                f"Diagram {diagram_id} is already {diagram.status}; save it to review again"
            )
        model = SemanticModelAdapter.validate_python(diagram.semantic_model)
        _ensure_layout_covers(diagram, model)
        validation_result = await self._validate_model(diagram, model)
        if not validation_result.is_valid:
            raise UmlApprovalValidationFailedError(
                f"検証エラーが{len(validation_result.errors)}件あるため承認できません"
            )

        diagram.status = STATUS_AFTER_APPROVE
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(diagram)
        return diagram

    async def export(
        self, *, project_id: uuid.UUID, diagram_id: uuid.UUID, fmt: ExportFormat
    ) -> ExportedFile:
        """承認済みの図をdraw.io/SVGに書き出す(M8)。出力に成功したら`approved`を`exported`に
        する(M7。状態が変わっても`version`は増やさない)。

        承認済み・出力済みの図だけを出力する(それ以外は409)。承認の条件で配置の有無は
        確かめているが、出力エンジン(`build_render`)も配置の無い要素を拒否する。
        """
        diagram = await self._get_owned(project_id=project_id, diagram_id=diagram_id)
        if not can_export(parse_status(diagram.status)):
            raise UmlDiagramNotApprovedError(
                f"Diagram {diagram_id} is {diagram.status}; approve it before exporting"
            )
        model = SemanticModelAdapter.validate_python(diagram.semantic_model)
        _ensure_layout_covers(diagram, model)
        layout = LayoutModel.model_validate(diagram.layout_model)
        render = build_render(
            model, layout, edge_labels(model, await self._data_item_names(diagram, model))
        )
        title = _diagram_title(model.notation, diagram.subject)
        content = (
            to_drawio(render, diagram_id=str(diagram.id), title=title)
            if fmt == "drawio"
            else to_svg(render)
        )

        diagram.status = STATUS_AFTER_EXPORT
        await self._session.flush()
        await self._session.commit()
        return ExportedFile(
            filename=_export_filename(model.notation, diagram.subject, fmt),
            content=content,
            media_type=_MEDIA_TYPES[fmt],
        )

    async def _validate_model(
        self,
        diagram: UmlDiagram,
        model: ComponentSemanticModel | ErSemanticModel | DfdSemanticModel,
    ) -> ValidationResult:
        """DFDなら、データ辞書全件のidと、同じプロジェクトの他のDFDが参照しているデータ項目のidを
        集めてから検証する(component/erはそのまま検証する)。"""
        if not isinstance(model, DfdSemanticModel):
            return validate_diagram(model)
        data_items = await self._data_items.list_for_project(diagram.project_id)
        referenced_elsewhere: set[uuid.UUID] = set()
        for other in await self._diagrams.list_by_notation(diagram.project_id, "dfd"):
            if other.id == diagram.id:
                continue
            other_model = SemanticModelAdapter.validate_python(other.semantic_model)
            if isinstance(other_model, DfdSemanticModel):
                referenced_elsewhere |= {flow.data_item_id for flow in other_model.relations}
        return validate_diagram(
            model,
            data_item_ids={item.id for item in data_items},
            referenced_elsewhere=referenced_elsewhere,
        )

    async def _data_item_names(
        self,
        diagram: UmlDiagram,
        model: ComponentSemanticModel | ErSemanticModel | DfdSemanticModel,
    ) -> dict[uuid.UUID, str]:
        """DFDの辺ラベル用に、データ項目id → 名前を引く(DFD以外はデータ辞書を読まない)。"""
        if not isinstance(model, DfdSemanticModel):
            return {}
        items = await self._data_items.list_for_project(diagram.project_id)
        return {item.id: item.name for item in items}

    async def _get_owned(self, *, project_id: uuid.UUID, diagram_id: uuid.UUID) -> UmlDiagram:
        diagram = await self._diagrams.get_by_id(diagram_id, project_id=project_id)
        if diagram is None:
            raise UmlDiagramNotFoundError(f"Diagram {diagram_id} not found")
        return diagram


def _diagram_title(notation: NotationType, subject: str) -> str:
    """図の題名(component・ER全体図はsubjectが空文字)。"""
    notation_title = _NOTATION_TITLES[notation]
    return f"{notation_title}: {subject}" if subject else f"{notation_title}(全体)"


def _export_filename(notation: NotationType, subject: str, fmt: ExportFormat) -> str:
    """出力するファイル名(`{notation}[_{subject}].{拡張子}`)。subjectはDFDの処理名などで
    `/`を含みうる(例: `DF-1: POST /api/v1/reservations`)ため、使えない文字を`_`に置き換える。"""
    base = f"{notation}_{subject}" if subject else notation
    return f"{_UNSAFE_FILENAME_CHARS.sub('_', base).strip()}.{fmt}"


def _ensure_version(diagram: UmlDiagram, expected_version: int) -> None:
    """楽観ロック。`expected_version`がDB上の現在のversionと一致しなければ409にする。"""
    if diagram.version != expected_version:
        raise UmlDiagramVersionConflictError(
            f"Diagram {diagram.id} has been updated by someone else "
            f"(expected version {expected_version}, current version {diagram.version})"
        )


def _ensure_layout_covers(
    diagram: UmlDiagram,
    model: ComponentSemanticModel | ErSemanticModel | DfdSemanticModel,
) -> None:
    """配置があり、意味モデルの全要素の座標を含むことを確かめる(承認・出力の前提)。"""
    if diagram.layout_model is None:
        raise UmlLayoutRequiredError(
            "配置がありません。自動レイアウトを実行してから承認してください"
        )
    layout = LayoutModel.model_validate(diagram.layout_model)
    missing = [el.id for el in model.elements if el.id not in layout.nodes]
    if missing:
        raise UmlLayoutRequiredError(
            f"配置の無い要素があります({', '.join(missing)})。"
            "保存するか、自動レイアウトを実行してください"
        )


def _ensure_not_generating(diagram: UmlDiagram) -> None:
    """AI生成中の図は、生成結果で上書きされるため更新・レイアウト実行を受け付けない。"""
    if diagram.generation_status == "generating":
        raise UmlGenerationInProgressError(
            f"Diagram {diagram.id} is being generated; retry after the generation finishes"
        )
