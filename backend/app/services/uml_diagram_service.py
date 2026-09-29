import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.models.uml_diagram import UmlDiagram
from app.repositories.data_item import DataItemRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.errors import (
    LayoutNodeLimitExceededError,
    LayoutValidationFailedError,
    UmlDiagramNotFoundError,
    UmlDiagramVersionConflictError,
)
from app.uml.domain import (
    NOTATION_TO_VIEW,
    ComponentSemanticModel,
    DfdSemanticModel,
    ErSemanticModel,
    NotationType,
    SemanticModelAdapter,
    empty_semantic_model,
)
from app.uml.layout import compute_layout as compute_layout_engine
from app.uml.validation import ValidationResult, validate_diagram
from app.uml.validation.structural import MAX_ELEMENTS


class UmlDiagramService:
    """UML設計図(component/er/dfd)に対するユースケース(生成トリガー・取得・更新・検証)を
    担当するサービス。"""

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._session = session
        self._diagrams = UmlDiagramRepository(session)
        self._data_items = DataItemRepository(session)

    async def list_for_project(self, project_id: uuid.UUID) -> list[UmlDiagram]:
        """指定プロジェクトのUML図一覧を取得する。"""
        return await self._diagrams.list_for_project(project_id)

    async def create(self, *, project_id: uuid.UUID, notation: NotationType) -> UmlDiagram:
        """UML図を新規作成する(status='draft', version=1)。

        `POST /projects/{id}/uml/diagrams`は本来「AIによる設計図の生成トリガー」(M1)だが、
        構造化出力によるAI生成自体はPhase 10で実装する。Phase 8時点では、指定notationの
        要素・関係が空の意味モデルを持つdraftを作成するプレースホルダーとして動作する
        (textbook/Phase-8/Phase-8-introduction.md参照。Phase 10でこのメソッドの内部を
        AI生成呼び出しに置き換える)。
        """
        view = NOTATION_TO_VIEW[notation]
        model = empty_semantic_model(notation)
        diagram = await self._diagrams.create(
            project_id=project_id,
            view=view,
            notation=notation,
            semantic_model=model.model_dump(mode="json"),
        )
        await self._session.commit()
        return diagram

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
    ) -> UmlDiagram:
        """UML図の意味モデル全体を更新する(楽観ロック)。

        `expected_version`がDB上の現在のversionと一致しない場合、他の更新と競合している
        とみなしUmlDiagramVersionConflictError(409)にする(devex-api既存コードベースに前例の
        無い新規パターン。generated_documents.versionは追記のたびに増える版数であり、
        書き込み競合検知の仕組みではない)。
        """
        diagram = await self._get_owned(project_id=project_id, diagram_id=diagram_id)
        if semantic_model.notation != diagram.notation:
            raise BadRequestError(
                f"notation は変更できません(現在: {diagram.notation}, "
                f"指定値: {semantic_model.notation})"
            )
        if diagram.version != expected_version:
            raise UmlDiagramVersionConflictError(
                f"Diagram {diagram_id} has been updated by someone else "
                f"(expected version {expected_version}, current version {diagram.version})"
            )
        diagram.semantic_model = semantic_model.model_dump(mode="json")
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
        idも渡す(診断8の未参照データ項目チェック等に使う)。"""
        diagram = await self._get_owned(project_id=project_id, diagram_id=diagram_id)
        model = SemanticModelAdapter.validate_python(diagram.semantic_model)

        data_item_ids: set[uuid.UUID] | None = None
        if isinstance(model, DfdSemanticModel):
            data_items = await self._data_items.list_for_project(project_id)
            data_item_ids = {item.id for item in data_items}

        return validate_diagram(model, data_item_ids=data_item_ids)

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
        model = SemanticModelAdapter.validate_python(diagram.semantic_model)

        if len(model.elements) > MAX_ELEMENTS:
            raise LayoutNodeLimitExceededError(
                f"要素数が上限({MAX_ELEMENTS})を超えています: {len(model.elements)}件"
            )

        data_item_ids: set[uuid.UUID] | None = None
        if isinstance(model, DfdSemanticModel):
            data_items = await self._data_items.list_for_project(project_id)
            data_item_ids = {item.id for item in data_items}
        validation_result = validate_diagram(model, data_item_ids=data_item_ids)
        if not validation_result.is_valid:
            messages = "; ".join(issue.message for issue in validation_result.errors)
            raise LayoutValidationFailedError(
                f"意味モデルの検証エラーのためレイアウトを計算できません: {messages}"
            )

        layout_model = await asyncio.to_thread(compute_layout_engine, str(diagram.id), model)
        diagram.layout_model = layout_model.model_dump(mode="json")
        await self._session.flush()
        await self._session.commit()
        # updated_at は server-side の onupdate=func.now() で決まるため、UPDATE後は
        # DBが計算した値を明示的に取り直す(update()と同じ理由)。
        await self._session.refresh(diagram)
        return diagram

    async def _get_owned(self, *, project_id: uuid.UUID, diagram_id: uuid.UUID) -> UmlDiagram:
        diagram = await self._diagrams.get_by_id(diagram_id, project_id=project_id)
        if diagram is None:
            raise UmlDiagramNotFoundError(f"Diagram {diagram_id} not found")
        return diagram
