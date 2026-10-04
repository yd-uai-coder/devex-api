"""詳細設計書の組み立ての入力と、章ごとの状態(純粋関数)。

docs/internal_design.md 3.3節「4. 詳細設計モード」の「詳細設計書の組み立て」。

詳細設計書(HTML・Markdown)は、承認済みの段階の意味モデルと図から決定的に組み立てる表示で、
文書としては保存しない。ここでは、組み立てに使う値を1つの`DocumentSource`にまとめる。
DB の読み取りと図の描画はサービス(app/services/detailed_design_export_service.py)が行い、
ここから先(導出・md・HTML)はすべて純粋関数にする。

章の状態は3つに分ける(Phase 22 の決定):

- `approved`: 段階が承認済み(古くない)。本文を組み立てる。
- `skipped`: 段階6を0件で承認した(段階6を飛ばした)。06章は「省略」と書く。
- `unapproved`: それ以外(未着手・下書き・レビュー中・古い)。章には「未承認」とだけ書く。
  承認していない内容は人が確定していないので、途中の内容を本文に出さない。

07 横断事項は段階7(横断事項と実装計画)の model から組み立てる(Phase 23 の決定)。段階7の
実装計画は詳細設計書とは別のファイル(implementation_plan.md・.html)にするが、入力は同じ
`DocumentSource`を使う。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from app.detailed_design.data_flow import DataFlowModel
from app.detailed_design.data_model import CrudModel
from app.detailed_design.function_list import FunctionListModel
from app.detailed_design.logic import LOGIC_STAGE, LogicModel
from app.detailed_design.plan import PlanModel
from app.detailed_design.procedure import ProcedureModel
from app.detailed_design.stages import StageState
from app.detailed_design.structure import ModuleListModel
from app.uml.domain.er import ErSemanticModel

ChapterStatus = Literal["approved", "skipped", "unapproved"]


@dataclass(frozen=True)
class Chapter:
    """詳細設計書の章1つ。章と段階は1対1(07 横断事項は段階7。Phase 23)。"""

    stage: int
    number: str
    title: str


CHAPTERS: tuple[Chapter, ...] = (
    Chapter(1, "01", "機能(処理)一覧"),
    Chapter(2, "02", "データフロー"),
    Chapter(3, "03", "データモデル"),
    Chapter(4, "04", "ソフトウェア構造"),
    Chapter(5, "05", "主要処理の手順"),
    Chapter(6, "06", "処理ロジックの詳細"),
    Chapter(7, "07", "横断事項"),
)


@dataclass(frozen=True)
class RenderedDiagram:
    """詳細設計書に載せる図1枚。`path`は zip の中の SVG のパス(md の画像の参照先)、
    `svg`は HTML に埋め込む SVG の本文。"""

    title: str
    path: str
    svg: str


@dataclass(frozen=True)
class DataItemEntry:
    """データ辞書の1行(データ項目)。`id`は DFD の線が参照する`data_item_id`の文字列。"""

    id: str
    name: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentSource:
    """詳細設計書の組み立てに使う値の全部。段階の model は、承認済みの段階のものだけを持つ
    (承認していない段階は`None`)。"""

    title: str
    chapters: Mapping[int, ChapterStatus]
    function_list: FunctionListModel | None = None
    data_flow: DataFlowModel | None = None
    crud: CrudModel | None = None
    modules: ModuleListModel | None = None
    procedures: ProcedureModel | None = None
    logics: LogicModel | None = None
    plan: PlanModel | None = None
    dfd_diagrams: Mapping[str, RenderedDiagram] = field(default_factory=dict)
    dfd_models: tuple[Mapping[str, Any], ...] = ()
    data_items: tuple[DataItemEntry, ...] = ()
    er: ErSemanticModel | None = None
    er_diagram: RenderedDiagram | None = None
    component_diagram: RenderedDiagram | None = None

    def status(self, stage: int) -> ChapterStatus:
        return self.chapters.get(stage, "unapproved")


def chapter_status(stage: int, state: StageState, model: Mapping[str, Any] | None) -> ChapterStatus:
    """段階の状態と内容から、章の状態を決める。段階6だけは、0件の承認を「省略」にする。"""
    if state != "approved":
        return "unapproved"
    if stage == LOGIC_STAGE and not LogicModel.model_validate(model or {}).logics:
        return "skipped"
    return "approved"


def chapter_statuses(
    states: Mapping[int, StageState], models: Mapping[int, Mapping[str, Any]]
) -> dict[int, ChapterStatus]:
    """01〜07章の状態を、段階の状態(`derive_states`の結果)と承認済みの段階の内容から決める。"""
    return {
        chapter.stage: chapter_status(
            chapter.stage, states.get(chapter.stage, "not_started"), models.get(chapter.stage)
        )
        for chapter in CHAPTERS
    }


def document_source(
    title: str,
    states: Mapping[int, StageState],
    models: Mapping[int, Mapping[str, Any]],
    *,
    dfd_diagrams: Mapping[str, RenderedDiagram] | None = None,
    dfd_models: tuple[Mapping[str, Any], ...] = (),
    data_items: tuple[DataItemEntry, ...] = (),
    er: ErSemanticModel | None = None,
    er_diagram: RenderedDiagram | None = None,
    component_diagram: RenderedDiagram | None = None,
) -> DocumentSource:
    """段階の状態と内容から`DocumentSource`を作る。承認していない段階の内容は渡さない(`None`)。
    省略の段階6は、0件の`LogicModel`として渡す。"""
    chapters = chapter_statuses(states, models)

    def raw(stage: int) -> Mapping[str, Any] | None:
        return models.get(stage) if chapters[stage] != "unapproved" else None

    return DocumentSource(
        title=title,
        chapters=chapters,
        function_list=_parse(FunctionListModel, raw(1)),
        data_flow=_parse(DataFlowModel, raw(2)),
        crud=_parse(CrudModel, raw(3)),
        modules=_parse(ModuleListModel, raw(4)),
        procedures=_parse(ProcedureModel, raw(5)),
        logics=_parse(LogicModel, raw(6)),
        plan=_parse(PlanModel, raw(7)),
        dfd_diagrams=dfd_diagrams or {},
        dfd_models=dfd_models,
        data_items=data_items,
        er=er,
        er_diagram=er_diagram,
        component_diagram=component_diagram,
    )


def _parse[M: BaseModel](model_type: type[M], raw: Mapping[str, Any] | None) -> M | None:
    return model_type.model_validate(raw) if raw is not None else None
