"""簡易ドキュメントモードの、単位が参照する設計の導出と展開(純粋関数)。

詳細設計モードの`unit_refs`(procedure_doc.py)・`unit_context`(procedure_doc_refs.py)の簡易モード版。
参照の鍵は、WBS の単位の「処理」(DF の ID)と「モジュール」(内部設計書のモジュール一覧のパス)。
展開した中身は保存しない(詳細設計モードと同じく、生成の入力・画面の単位の詳細・AI 向けの出力の
3か所が同じ関数を使う)。

- DF の参照には、流れの表とデータ項目に加えて、同じ API の内部設計書 3.3・外部設計書 2.6 の行と、
  流れに出てくる 3.2節のテーブルを添える(作成方針 10章: API の契約・データ)。DF を持たない単位
  (基盤の単位など)には、3.2節のデータモデル全体を添える(`datamodel`)。
- モジュールは層まで照合する(`module_layer`)。簡易モードのモジュール一覧は層ごとにまとめた行なので、
  ファイルの属する層の行を展開する。どの層にも当たらないファイル(`Dockerfile`・設定など)は参照に
  出さない(簡易モードの粒度では、一覧に無くて当然のため)。
- 共通の節は、内部設計書 3.4節(エラー・ログ。07章の代わり)と、実装計画書 4.3節・内部設計書
  3.1節(技術スタック・開発環境)。
- 関数の契約とシーケンス図は無い(`ExpandedRef.svg`・`mermaid`は None)。
"""

from app.detailed_design.api_list import endpoint_key
from app.detailed_design.plan import PlanTask
from app.detailed_design.procedure_doc import DesignRef, PlanUnit
from app.detailed_design.procedure_doc_refs import ExpandedRef, UnitContext
from app.detailed_design.simple_procedure.internal_design import SimpleDesignBook, module_layer

INTERNAL_DESIGN_LABEL = "内部設計書"
DATA_MODEL_KEY = "3.2"


def simple_unit_refs(task: PlanTask, book: SimpleDesignBook) -> list[DesignRef]:
    """単位が参照する設計を導く(処理別データフロー → データモデル → モジュールの順)。

    DF を持たない単位には 3.2節のデータモデル全体を添える(3.2節が空なら添えない)。モジュールは
    層の決まるものだけを出す。"""
    refs = [DesignRef("dataflow", key, key in book.dataflows) for key in _clean(task.function_ids)]
    if not refs and book.data_model.strip():
        refs.append(DesignRef("datamodel", DATA_MODEL_KEY, True))
    refs += [
        DesignRef("module", path, True)
        for path in _clean(task.modules)
        if module_layer(path, book) is not None
    ]
    return refs


def simple_ref_label(ref: DesignRef, book: SimpleDesignBook) -> str:
    """参照の見出し(`内部設計書 DF-1 POST /api/v1/x`・`内部設計書 3.2 データモデル`・
    `内部設計書 3.3 `app/x.py``)。"""
    if ref.kind == "dataflow":
        flow = book.dataflows.get(ref.key)
        return f"{INTERNAL_DESIGN_LABEL} {ref.key} {flow.title if flow else ''}".rstrip()
    if ref.kind == "datamodel":
        return f"{INTERNAL_DESIGN_LABEL} 3.2 データモデル"
    return f"{INTERNAL_DESIGN_LABEL} 3.3 `{ref.key}`"


def expand_simple_ref(ref: DesignRef, book: SimpleDesignBook) -> str | None:
    """参照1つを、内部設計書・外部設計書の該当箇所の md にする(解決できない参照は None)。"""
    if not ref.resolved:
        return None
    if ref.kind == "dataflow":
        flow = book.dataflows.get(ref.key)
        if flow is None:
            return None
        body = flow.markdown or "(流れの表がありません)"
        lines = [f"### {simple_ref_label(ref, book)}", "", body]
        api_lines = _api_lines(flow.trigger, book)
        if api_lines:
            lines += ["", *api_lines]
        for name in flow.tables:
            table = book.tables.get(name)
            if table:
                lines += ["", f"#### {INTERNAL_DESIGN_LABEL} 3.2 テーブル: {name}", "", table]
        return "\n".join(lines)
    if ref.kind == "datamodel":
        text = book.data_model.strip()
        return f"### {simple_ref_label(ref, book)}\n\n{text}" if text else None
    module = module_layer(ref.key, book)
    if module is None:
        return None
    depends = ", ".join(module.depends_on) or "なし"
    row = f"({module.layer or '—'}): {module.responsibility or '—'} / 依存先: {depends}"
    if module.path == ref.key.strip():
        return f"- {INTERNAL_DESIGN_LABEL} 3.3 `{module.path}`{row}"
    return f"- {INTERNAL_DESIGN_LABEL} 3.3 `{ref.key}` → 層 `{module.path}`{row}"


def _api_lines(trigger: tuple[str, str] | None, book: SimpleDesignBook) -> list[str]:
    """DF の見出しの API に当たる、内部設計書 3.3・外部設計書 2.6 の行。"""
    if trigger is None:
        return []
    key = endpoint_key(*trigger)
    lines: list[str] = []
    internal = book.apis.get(key)
    if internal is not None:
        lines.append(f"- API({INTERNAL_DESIGN_LABEL} 3.3): {key} ── {internal.summary or '—'}")
    external = book.external_apis.get(key)
    if external is not None:
        screens = "/".join(external.screens) or "—"
        lines.append(
            f"- API(外部設計書 2.6): {key} ── {external.summary or '—'}(関連画面: {screens})"
        )
    return lines


def crosscutting_section(book: SimpleDesignBook) -> str:
    """内部設計書 3.4節(例外処理・エラーハンドリング・ログ)の md(書かれていなければ空)。"""
    text = book.error_policy.strip()
    return f"### {INTERNAL_DESIGN_LABEL} 3.4 例外処理・エラー・ログ\n\n{text}" if text else ""


def environment_section(book: SimpleDesignBook, environment: str) -> str:
    """実装計画書 4.3節(開発環境)と内部設計書 3.1節(技術スタック)の md(無ければ空)。"""
    parts: list[str] = []
    if environment.strip():
        parts.append(f"### 実装計画書 4.3 開発環境\n\n{environment.strip()}")
    if book.architecture.strip():
        parts.append(
            f"### {INTERNAL_DESIGN_LABEL} 3.1 技術スタック・アーキテクチャ\n\n"
            f"{book.architecture.strip()}"
        )
    return "\n\n".join(parts)


def simple_unit_context(
    unit: PlanUnit, book: SimpleDesignBook, environment: str
) -> UnitContext:
    """単位の参照を導いて展開し、共通の節と合わせる(`environment`は実装計画書 4.3節の本文)。"""
    refs = tuple(
        ExpandedRef(
            kind=ref.kind,
            key=ref.key,
            resolved=ref.resolved,
            via=ref.via,
            label=simple_ref_label(ref, book),
            markdown=expand_simple_ref(ref, book),
        )
        for ref in simple_unit_refs(unit.task, book)
    )
    return UnitContext(
        unit=unit,
        refs=refs,
        crosscutting=crosscutting_section(book),
        environment=environment_section(book, environment),
    )


def _clean(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))
