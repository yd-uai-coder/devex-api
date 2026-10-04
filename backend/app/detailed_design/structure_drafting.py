"""段階4(ソフトウェア構造)のAIの下書きの入出力(純粋関数。docs/external_design.md 2.7節)。

1回の生成で、次の2種類を順に呼ぶ:

- 構成図: 入力は、要件定義書(技術スタック)・機能一覧・処理概要表・ER のテーブル名(LLM 1回)。
  出力スキーマと意味モデルへの写像は、ステージ3のコンポーネント図(`ComponentGenerationOutput`・
  `to_component`)をそのまま使う。プロンプトだけ段階4専用にする(箱をパッケージ単位にし、層を
  レーンにする。簡易ドキュメントモードの component のプロンプトは変えない)。
- モジュール一覧: 入力は、先に作った構成図の要素と層・機能一覧・処理概要表・CRUD 図・ER の
  テーブル名・要件定義書(LLM 1回)。

モジュール一覧の層は、構成図の層の名前から選ばせる(検証で、構成図に無い層を警告する)。
「関わる処理」は処理IDで書かせ、機能一覧に無いIDは`merge_modules`が捨てる。
"""

from collections.abc import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.data_flow import ProcessSummaryRow
from app.detailed_design.data_model import CrudCell
from app.detailed_design.function_list import FunctionRow
from app.detailed_design.prompt_rules import NAMING_RULES
from app.detailed_design.structure import ModuleDraft
from app.uml.domain.component import ComponentSemanticModel
from app.uml.validation.structural import MAX_ELEMENTS

# 構成図の箱の数の目安(パッケージ単位の俯瞰図にするため。図の上限 MAX_ELEMENTS より小さくする)
MAX_COMPONENTS = 15


class GeneratedModuleRow(BaseModel):
    """AIが下書きするモジュール一覧の1行。"""

    path: str = Field(
        description="ファイルのパス(例: app/services/project.py)。"
        "似たファイルは app/repositories/{a,b}.py や app/models/*.py のようにまとめてよい"
    )
    layer: str = Field(description="【構成図】の層の名前から1つ選んでそのまま書く")
    responsibility: str = Field(description="そのファイルの責務を日本語の1文で")
    depends_on: list[str] = Field(
        description="主な依存先。この一覧の他のモジュールはそのパス(またはそのディレクトリ)で、"
        "外部のライブラリは名前で書く"
    )
    functions: list[str] = Field(
        description="関わる処理の処理ID(【機能一覧】の F-01 など)。全処理が通るモジュールは空にする"
    )
    all_functions: bool = Field(
        description="アプリの組み立て・認証の DI・例外の変換のように、"
        "全処理が通るモジュールなら true"
    )


class ModuleListGenerationOutput(BaseModel):
    """モジュール一覧の構造化出力。"""

    modules: list[GeneratedModuleRow]


COMPONENT_SYSTEM_PROMPT = (
    "あなたはソフトウェアアーキテクトです。【要件定義】の技術スタックと【機能一覧】【処理概要表】"
    "【テーブル】から、このシステムの構成図(層)の意味モデルを作成します。\n"
    "規則:\n"
    "- modules はパッケージ(ディレクトリ)単位の箱にする(ファイル単位にしない)。"
    f"合計{MAX_COMPONENTS}個以内に収める\n"
    "- layer は層の名前(例: 入口, ユースケース, ドメイン, 外部連携, 永続化)で、"
    "図のレーン(列)になる。"
    "同じ層には同じ文字列を使い、層は呼び出しの上流から下流の順に現れるように並べる\n"
    "- name は『日本語の名称(実際に作るディレクトリ)』の形にする"
    "(例: ルーター(api/routes)、サービス(services)、リポジトリ(repositories))\n"
    "- description にはパッケージの責務を短く書く\n"
    "- dependencies は呼び出し・import の向き(依存する側 → 依存される側)で表す\n"
    "- 設定・ログ・入出力の型のように、ほぼ全層から使う定型のパッケージは省く\n"
    f"- 要素は合計{MAX_ELEMENTS}個以内にし、ID は図の中で一意にする\n"
    "- 入力に書かれていない業務のパッケージを創作しない"
    + NAMING_RULES
)

MODULE_SYSTEM_PROMPT = (
    "あなたは詳細設計の担当者です。【構成図】のパッケージを、ファイル単位のモジュール一覧"
    "(パス / 層 / 責務 / 主な依存先 / 関わる処理)にします。\n"
    "規則:\n"
    "- path は【構成図】のパッケージの下のファイルのパスにする。同じパスを2回書かない\n"
    "- layer は、そのファイルが属する【構成図】のパッケージの層の名前をそのまま書く\n"
    "- functions には、そのモジュールが関わる処理の処理IDを書く。テーブルを読み書きするモジュールは"
    "【CRUD図】でそのテーブルを使う処理を手がかりにする\n"
    "- 全処理が通るモジュール(アプリの組み立て・認証の DI・例外の変換)は"
    " all_functions を true にし、"
    "functions を空にする\n"
    "- 全ての処理が、少なくとも1つのモジュールの functions に現れるようにする\n"
    "- 入出力の型・設定のような定型のファイルは省くか1行にまとめる\n"
    "- 処理ID・層の名前は入力の値をそのまま書き、入力に無い処理を創作しない"
    + NAMING_RULES
)


def _function_lines(functions: Sequence[FunctionRow]) -> str:
    return "\n".join(
        f"- {f.id} {f.name}(種別: {f.kind} / トリガー: {f.trigger} / 機能グループ: {f.group})"
        for f in functions
    )


def _summary_lines(summaries: Sequence[ProcessSummaryRow]) -> str:
    return "\n".join(
        f"- {s.function_id}: 入力={s.input} / 処理={s.process} / 出力={s.output}"
        for s in summaries
    )


def _table_lines(tables: Sequence[str]) -> str:
    return "\n".join(f"- {t}" for t in tables)


def build_component_messages(
    requirements: str,
    functions: Sequence[FunctionRow],
    summaries: Sequence[ProcessSummaryRow],
    tables: Sequence[str],
) -> list[BaseMessage]:
    """構成図の下書きの入力。要件定義書は全文を渡す(技術スタックが 1.6 制約条件などに散るため)。"""
    content = "\n\n".join(
        [
            f"## 要件定義\n{requirements or '(ありません)'}",
            f"## 機能一覧\n{_function_lines(functions)}",
            f"## 処理概要表\n{_summary_lines(summaries) or '(ありません)'}",
            f"## テーブル\n{_table_lines(tables) or '(ありません)'}",
        ]
    )
    return [SystemMessage(content=COMPONENT_SYSTEM_PROMPT), HumanMessage(content=content)]


def build_module_messages(
    component: ComponentSemanticModel,
    requirements: str,
    functions: Sequence[FunctionRow],
    summaries: Sequence[ProcessSummaryRow],
    cells: Sequence[CrudCell],
    tables: Sequence[str],
) -> list[BaseMessage]:
    """モジュール一覧の下書きの入力。構成図は要素(名前・層・責務)と依存の向きを文で渡す。"""
    names = {element.id: element.name for element in component.elements}
    packages = "\n".join(
        f"- {e.name}(層: {e.layer or '未設定'}){e.description or ''}" for e in component.elements
    )
    dependencies = "\n".join(
        f"- {names.get(r.source_id, r.source_id)} → {names.get(r.target_id, r.target_id)}"
        for r in component.relations
    )
    crud = "\n".join(f"- {c.function_id} × {c.table}: {c.ops}" for c in cells)
    content = "\n\n".join(
        [
            f"## 構成図\n{packages or '(ありません)'}"
            f"\n\n依存の向き:\n{dependencies or '(ありません)'}",
            f"## 要件定義\n{requirements or '(ありません)'}",
            f"## 機能一覧\n{_function_lines(functions)}",
            f"## 処理概要表\n{_summary_lines(summaries) or '(ありません)'}",
            f"## CRUD図\n{crud or '(ありません)'}",
            f"## テーブル\n{_table_lines(tables) or '(ありません)'}",
        ]
    )
    return [SystemMessage(content=MODULE_SYSTEM_PROMPT), HumanMessage(content=content)]


def to_module_drafts(output: ModuleListGenerationOutput) -> list[ModuleDraft]:
    """構造化出力を、merge_modules の入力に変える。"""
    return [
        ModuleDraft(
            path=m.path,
            layer=m.layer,
            responsibility=m.responsibility,
            depends_on=tuple(m.depends_on),
            functions=tuple(m.functions),
            all_functions=m.all_functions,
        )
        for m in output.modules
    ]
