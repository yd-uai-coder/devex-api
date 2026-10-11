"""要件定義書の model から md を組み立てる(純粋関数)。

見出しは以前の要件定義書(1.1〜1.6)のままにし、後続(外部設計書の生成・段階2/4/7/8・
自己診断・手順書の対象外)が今と同じく読めるようにする。1.4 は表の行になるので、手順書の対象外
(`procedure_output.source.out_of_scope_lines`)は表の行も読む。
"""

from collections.abc import Sequence

from app.detailed_design.document.markdown import md_table
from app.documents.requirements.model import (
    SOURCE_LABEL,
    RequirementKind,
    RequirementRow,
    RequirementsModel,
    RequirementSource,
)
from app.hearing import POINT_DEFS, SCALE_LABEL, STATUS_LABEL, as_scale, point_def

TITLE = "# 1. 要件定義書"
LEGEND = (
    "出どころ: **ユーザー** = 初期入力・チャットでの回答より / "
    "**採用** = ヒアリングで AI の提案を採用 / "
    "**推測** = ドキュメント生成時に AI が推測(未確認。利用者の確認を待つ)。"
)
_NONE_ROW = "(なし)"


def source_cell(source: RequirementSource) -> str:
    """出どころの欄。推測は目立たせる。"""
    label = SOURCE_LABEL[source]
    return f"**{label}**" if source == "assumed" else label


def points_cell(points: Sequence[str]) -> str:
    return " / ".join(point_def(key).label for key in points) or "—"


def _rows_of(model: RequirementsModel, kind: RequirementKind) -> list[RequirementRow]:
    return [r for r in model.rows if r.kind == kind]


def _requirement_table(
    rows: Sequence[RequirementRow], group_header: str, text_header: str
) -> list[str]:
    if not rows:
        return [_NONE_ROW]
    body = [
        [
            r.id,
            r.priority if r.kind == "FR" and r.priority else r.category,
            r.text,
            source_cell(r.source),
            points_cell(r.points),
        ]
        for r in rows
    ]
    lines = md_table(["ID", group_header, text_header, "出どころ", "観点"], body)
    assumed = [r.id for r in rows if r.source == "assumed"]
    if assumed:
        lines += [
            "",
            f"- 推測の要件({'・'.join(assumed)})は、AI が補ったもの(利用者は言っていない)。"
            "利用者に確かめてください。",
        ]
    return lines


def _appendix(model: RequirementsModel) -> list[str]:
    status_text: dict[str, str] = {**STATUS_LABEL}
    scale = as_scale(model.scale)
    body: list[list[str]] = []
    for d in POINT_DEFS:
        status = model.point_statuses.get(d.key, "open")
        text = status_text.get(status, status_text["open"])
        if d.key == "scale" and scale is not None and status != "open":
            text = f"{text}({SCALE_LABEL[scale]})"
        refs = [r.id for r in model.rows if d.key in r.points]
        if d.key == "purpose" and model.purpose:
            refs.insert(0, "1.1")
        if d.key in ("users", "daily_flow") and model.users:
            refs.insert(0, "1.2")
        if d.key == "scope" and model.out_of_scope:
            refs.insert(0, "1.3")
        body.append([d.label, text, "・".join(refs) or "—"])
    return md_table(["観点", "状態", "要件"], body)


def render_requirements(model: RequirementsModel) -> str:
    """要件定義書の md。付録に観点の状態(生成した時点の表。`model.point_statuses`)を載せる。"""
    lines = [TITLE, "", LEGEND, "", "## 1.1 プロジェクトの目的・背景", "", model.purpose or "—"]
    lines += ["", "## 1.2 ターゲットユーザー・利用シーン", ""]
    if model.users:
        lines += md_table(
            ["利用者", "使う場面", "出どころ"],
            [[u.user, u.scene, source_cell(u.source)] for u in model.users],
        )
    else:
        lines.append(_NONE_ROW)
    lines += ["", "## 1.3 システム化の範囲(In Scope / Out of Scope)", ""]
    lines += [f"- In Scope: {item}" for item in model.in_scope] or ["- In Scope: —"]
    lines += [f"- Out of Scope: {item}" for item in model.out_of_scope] or ["- Out of Scope: —"]
    lines += ["", "## 1.4 機能要件(MoSCoW 優先度)", ""]
    lines += _requirement_table(_rows_of(model, "FR"), "優先度", "要件")
    lines += ["", "## 1.5 非機能要件", ""]
    lines += _requirement_table(_rows_of(model, "NFR"), "大項目", "要件")
    lines += ["", "## 1.6 制約条件・前提条件", ""]
    lines += _requirement_table(_rows_of(model, "CON"), "種類", "内容")
    lines += ["", "## 付録 観点の状態(ヒアリングの結果)", ""]
    lines += _appendix(model)
    return "\n".join(lines) + "\n"
