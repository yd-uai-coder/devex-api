"""外部設計書の AI の下書きの入出力(純粋関数)。

AI には要件定義書から、画面・API・外部連携の行(関係する要件ID 付き)と本文の節を書かせる。
画面ID は仮の ID として書かせ(前の版と同じ画面なら前の版の ID)、`assemble.build_model`が前の版に
在る ID だけを引き継いで振り直す。再生成では、前の版の画面・API・外部連携の行と要件定義書の
変わった行を見せ、変えない行を`unchanged`と答えさせる(`app.documents.revision`)。合意済みの行には
印を付ける(生成の後に合意の形へ戻すのは app/agreements/protection.py)。
"""

import json
from collections.abc import Iterable, Sequence
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.detailed_design.api_list import endpoint_key
from app.documents.external_design.assemble import (
    ApiDraft,
    ExternalDesignDraft,
    IntegrationDraft,
    ScreenDraft,
)
from app.documents.external_design.model import NO_BODY, ExternalDesignModel
from app.documents.requirements.drafting import AGREED_MARK
from app.documents.revision import (
    NONE_TEXT,
    UNCHANGED_DESCRIPTION,
    RevisionInputs,
    changed_rows_text,
    instruction_text,
    revision_rule,
)

_REQ_IDS_DESCRIPTION = "関係する要件ID(【要件定義書】の FR-001・NFR-001・CON-001 など。1つ以上)"


class GeneratedScreen(BaseModel):
    """AI が下書きする画面1行(構造化出力のスキーマ)。"""

    id: str = Field(
        description="画面ID(SCR-001 の形)。【前の版の画面】に同じ画面があれば、その ID。"
        "新しい画面は前の版と重ならない ID"
    )
    name: str = Field(description="画面名")
    role: str = Field(description="主要な役割(1文)")
    priority: Literal["Must", "Should", "Could"] = Field(
        description="優先度(画面が担う要件の MoSCoW に合わせる)"
    )
    req_ids: list[str] = Field(description=_REQ_IDS_DESCRIPTION)
    ui_spec: str = Field(
        default="",
        description="主要画面の UI/UX 仕様(構成要素: 入力項目・表示項目・操作ボタン・アクション)。"
        "Markdown の箇条書き。見出しは書かない。主要でない画面は空",
    )
    unchanged: bool = Field(default=False, description=UNCHANGED_DESCRIPTION)


class GeneratedApi(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(
        description="`/api/v1/<リソース>`の形。個別の対象は`{id}`、親リソースに属するものは"
        "`/api/v1/<親リソース>/{id}/<リソース>`"
    )
    summary: str = Field(description="概要(短い句)")
    screens: list[str] = Field(description="関連画面の画面ID(screens の id)。無ければ空")
    req_ids: list[str] = Field(description=_REQ_IDS_DESCRIPTION)
    body: str = Field(
        default="",
        description="リクエストの本文の主な項目(`email, password`など)。POST・PUT・PATCH は必ず"
        f"書き、本文の要らないもの(生成の開始など)は`{NO_BODY}`と書く。GET・DELETE は空",
    )
    response: str = Field(description="成功時の応答(`201 予約`など)")
    errors: str = Field(default="", description="主なエラー(`404 対象なし、409 重複`など)")
    unchanged: bool = Field(
        default=False,
        description=f"{UNCHANGED_DESCRIPTION}。前の版の同じメソッド・パスの API を指す",
    )


class GeneratedIntegration(BaseModel):
    service: str = Field(description="連携する外部サービス・API")
    purpose: str = Field(description="用途")
    auth: str = Field(description="認証方式")
    timing: str = Field(description="データ連携のタイミング")
    req_ids: list[str] = Field(description=_REQ_IDS_DESCRIPTION)


class ExternalDesignGenerationOutput(BaseModel):
    """外部設計書の下書きの構造化出力。"""

    overview: str = Field(
        description="2.1 システム構成概要(システムの全体像と外部との関係。Markdown。"
        "構成はコードブロックのテキスト図で示してよい)"
    )
    screens: list[GeneratedScreen] = Field(description="2.2 画面一覧(2.3 の UI 仕様を含む)")
    transition: str = Field(
        description="2.2 画面遷移(画面ID と矢印のテキスト図。コードブロックの囲みは書かない)"
    )
    integrations: list[GeneratedIntegration] = Field(
        description="2.4 外部システム・API 連携。無ければ空"
    )
    data_io: str = Field(
        description="2.5 データ入出力仕様(ファイル入出力・受付フォーマット・バリデーション。"
        "Markdown。無ければ「なし」)"
    )
    apis: list[GeneratedApi] = Field(description="2.6 本システム自身が提供する API をすべて")


# 再生成のプロンプトの節の分け方(前の版・きっかけ・参照)。節を足したら、ここにも分ける
REVISION = RevisionInputs(
    previous=("前の版の画面", "前の版の API", "前の版の外部連携"),
    triggers={
        "上流の変わった行": "要件定義書の要件で変わった行。その行に関係する行を書き直す",
        "修正指示": "設計レビューの採用した指摘。指示が指す行を書き直す",
    },
    context=("要件定義書",),
)

SYSTEM_PROMPT = (
    "あなたは優秀なUI/UXデザイナー・システムアーキテクトです。【要件定義書】に基づき、"
    "ユーザー視点・外部仕様に関する外部設計書の中身を、画面・API・外部連携の行として書きます。\n"
    "規則:\n"
    "- 画面・API・外部連携の各行に、関係する要件ID(【要件定義書】の ID)を書く。"
    "【要件定義書】に無い ID を作らない\n"
    "- Must の機能要件には、それを担う画面か API を必ず置く。Won't の機能要件の画面・API は"
    "作らない\n"
    "- API は本システム自身が提供するものをすべて書く。POST・PUT・PATCH には本文の項目を書く"
    f"(本文の要らないものは「{NO_BODY}」)\n"
    "- 画面ID は SCR-001 の形。【前の版の画面】に同じ画面があれば、その ID を使う。"
    "API の関連画面は画面ID で書く\n"
    "- 主要画面には UI 仕様(構成要素)を書く\n"
    "- 要件定義書に無い技術・外部サービスを創作しない\n"
    "- 外部連携のサービス名は、【前の版の外部連携】に同じサービスがあれば、その名前のまま書く\n"
    f"- 前の版の行で「{AGREED_MARK}」の印が付いた行は、クライアントと合意した行。"
    "【上流の変わった行】【修正指示】がその行を指していなければ、unchanged を true にして"
    "必ず残す(書き直さない・落とさない。同じ画面を別の画面ID で書かない)\n"
    f"{revision_rule(REVISION)}"
)


def previous_screens_text(
    previous: ExternalDesignModel | None, agreed_screen_ids: frozenset[str] = frozenset()
) -> str:
    """前の版の画面(画面ID の引き継ぎと、変えない行の判断に使う)。無ければ「なし」。"""
    if previous is None or not previous.screens:
        return NONE_TEXT
    return _marked_rows_text((s, s.id in agreed_screen_ids) for s in previous.screens)


def _marked_rows_text(rows: Iterable[tuple[BaseModel, bool]]) -> str:
    """前の版の行(1行1つの JSON)。合意済みの行は末尾に印を付ける。無ければ「なし」。"""
    lines = [
        json.dumps(row.model_dump(mode="json"), ensure_ascii=False)
        + (f"|{AGREED_MARK}" if agreed else "")
        for row, agreed in rows
    ]
    return "\n".join(lines) or NONE_TEXT


def build_external_design_messages(
    requirements_md: str,
    previous: ExternalDesignModel | None,
    changed_requirements: Sequence[str] = (),
    instruction: str = "",
    *,
    agreed_screen_ids: frozenset[str] = frozenset(),
    agreed_endpoints: frozenset[str] = frozenset(),
    agreed_services: frozenset[str] = frozenset(),
) -> list[BaseMessage]:
    """外部設計書の生成のメッセージ。`agreed_*`は守る合意(画面ID・`endpoint_key`・サービス名。
    前の版の行に印を付ける)。"""
    apis = previous.apis if previous else []
    integrations = previous.integrations if previous else []
    apis_text = _marked_rows_text(
        (a, endpoint_key(a.method, a.path) in agreed_endpoints) for a in apis
    )
    integrations_text = _marked_rows_text(
        (i, i.service.strip() in agreed_services) for i in integrations
    )
    human = (
        f"## 前の版の画面\n{previous_screens_text(previous, agreed_screen_ids)}\n\n"
        f"## 前の版の API\n{apis_text}\n\n"
        f"## 前の版の外部連携\n{integrations_text}\n\n"
        f"## 上流の変わった行\n{changed_rows_text(changed_requirements)}\n\n"
        f"## 修正指示\n{instruction_text(instruction)}\n\n"
        f"## 要件定義書\n{requirements_md}"
    )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=human)]


def to_draft(output: ExternalDesignGenerationOutput) -> ExternalDesignDraft:
    return ExternalDesignDraft(
        overview=output.overview,
        transition=output.transition,
        data_io=output.data_io,
        screens=tuple(
            ScreenDraft(
                s.id, s.name, s.role, s.priority, tuple(s.req_ids), s.ui_spec, s.unchanged
            )
            for s in output.screens
        ),
        apis=tuple(
            ApiDraft(
                a.method,
                a.path,
                a.summary,
                tuple(a.screens),
                tuple(a.req_ids),
                a.body,
                a.response,
                a.errors,
                a.unchanged,
            )
            for a in output.apis
        ),
        integrations=tuple(
            IntegrationDraft(g.service, g.purpose, g.auth, g.timing, tuple(g.req_ids))
            for g in output.integrations
        ),
    )
