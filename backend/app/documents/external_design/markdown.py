"""外部設計書の model から md を組み立てる(純粋関数)。

見出しの番号(2.1〜2.6)と、2.6 の列名「メソッド/パス/概要/関連画面」は以前の外部設計書のままに
し、後ろに「要件/本文/応答/エラー」を足す。後続(段階1の機能一覧と`MISSING_API`・簡易モードの
内部設計書と段階8の`extract_api_endpoints`)は、列を見出しの名前で探すので今と同じく読める。
"""

from collections.abc import Sequence

from app.detailed_design.document.markdown import md_table
from app.documents.external_design.model import ExternalDesignModel
from app.documents.external_design.validation import covered_by

TITLE = "# 2. 外部設計書"
_NONE_ROW = "(なし)"
_FENCE = "```"


def ids_cell(ids: Sequence[str]) -> str:
    return "・".join(ids) or "—"


def _body(text: str) -> list[str]:
    return [text] if text else ["—"]


def _diagram(text: str) -> list[str]:
    """画面遷移のテキスト図。AI がコードブロックで囲んでいても、囲み直さない。"""
    if not text:
        return ["—"]
    if text.lstrip().startswith(_FENCE):
        return [text]
    return [f"{_FENCE}text", text, _FENCE]


def _screens(model: ExternalDesignModel) -> list[str]:
    if not model.screens:
        return [_NONE_ROW]
    return md_table(
        ["画面ID", "画面名", "主要な役割", "優先度", "要件"],
        [[s.id, s.name, s.role, s.priority, ids_cell(s.req_ids)] for s in model.screens],
    )


def _ui_specs(model: ExternalDesignModel) -> list[str]:
    lines: list[str] = []
    for s in model.screens:
        if s.ui_spec:
            lines += [f"### {s.id}: {s.name}", "", s.ui_spec, ""]
    return lines[:-1] if lines else [_NONE_ROW]


def _integrations(model: ExternalDesignModel) -> list[str]:
    if not model.integrations:
        return [_NONE_ROW]
    return md_table(
        ["サービス", "用途", "認証", "タイミング", "要件"],
        [[g.service, g.purpose, g.auth, g.timing, ids_cell(g.req_ids)] for g in model.integrations],
    )


def _apis(model: ExternalDesignModel) -> list[str]:
    if not model.apis:
        return [_NONE_ROW]
    return md_table(
        ["メソッド", "パス", "概要", "関連画面", "要件", "本文", "応答", "エラー"],
        [
            [
                a.method,
                a.path,
                a.summary,
                " / ".join(a.screens) or "—",
                ids_cell(a.req_ids),
                a.body,
                a.response,
                a.errors,
            ]
            for a in model.apis
        ],
    )


def _appendix(model: ExternalDesignModel) -> list[str]:
    if not model.must_fr_ids:
        return ["(要件定義書の Must の機能要件がありません)"]
    body: list[list[str]] = []
    for req_id in model.must_fr_ids:
        screens, apis = covered_by(model, req_id)
        body.append([req_id, ids_cell(screens), ids_cell(apis)])
    return md_table(["要件", "画面", "API"], body)


def render_external_design(model: ExternalDesignModel) -> str:
    """外部設計書の md。付録に Must の機能要件ごとの画面・API を載せる。"""
    lines = [TITLE, "", "## 2.1 システム構成概要", "", *_body(model.overview)]
    lines += ["", "## 2.2 画面一覧・画面遷移フロー（概要）", "", *_screens(model)]
    lines += ["", *_diagram(model.transition)]
    lines += ["", "## 2.3 主要画面のUI/UX仕様", "", *_ui_specs(model)]
    lines += ["", "## 2.4 外部システム・API連携仕様", "", *_integrations(model)]
    lines += ["", "## 2.5 データ入出力仕様", "", *_body(model.data_io)]
    lines += ["", "## 2.6 API一覧", "", *_apis(model)]
    lines += ["", "## 付録 要件との対応(Must の機能要件)", "", *_appendix(model)]
    return "\n".join(lines) + "\n"
