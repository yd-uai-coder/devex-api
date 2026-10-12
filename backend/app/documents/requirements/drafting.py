"""要件定義書の AI の下書きの入出力(純粋関数)。

AI には会話と観点の表から、要件の中身・出どころ・根拠の観点を書かせる。ID は書かせず、前の版の
同じ要件の ID を`prev_id`で指させるだけにする(`assemble.assign_ids`が決める)。出どころは
観点の表の状態を上限に、`assemble.cap_source`が直す。再生成では、変えない要件を`unchanged`と
答えさせ、前の版の行をそのまま使う(`app.documents.revision`)。
"""

from collections.abc import Mapping
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.documents.requirements.assemble import (
    RequirementDraft,
    RequirementsDraft,
    UserSceneDraft,
)
from app.documents.requirements.model import RequirementsModel
from app.documents.revision import NONE_TEXT, REVISION_RULE, UNCHANGED_DESCRIPTION
from app.hearing import POINT_DEFS, SCALE_LABEL, STATUS_LABEL, PointState, Scale

_SOURCE_DESCRIPTION = (
    "出どころ。user: 利用者が自分で言ったこと / adopted: AI の提案(選択肢カードなど)を利用者が"
    "選んだこと / assumed: 利用者は言っていないが AI が補ったこと"
)


class GeneratedRequirement(BaseModel):
    """AI が下書きする要件1行(構造化出力のスキーマ)。"""

    kind: Literal["FR", "NFR", "CON"] = Field(
        description="FR: 機能要件 / NFR: 非機能要件 / CON: 制約条件・前提条件"
    )
    prev_id: str = Field(
        default="",
        description="【前の版の要件】に同じ要件があれば、その ID(FR-001 など)。新しい要件は空",
    )
    priority: Literal["Must", "Should", "Could", "Won't", ""] = Field(
        default="", description="FR だけ MoSCoW の優先度。NFR・CON は空"
    )
    category: str = Field(
        default="",
        description="NFR は IPA の大項目(可用性・性能・拡張性・運用・保守性・移行性・"
        "セキュリティ・システム環境・エコロジー)。CON は種類(技術スタック・外部サービス・"
        "配置・体制・期間・予算など)。FR は空",
    )
    text: str = Field(description="要件を1〜2文で。利用者の言葉と決めたことをそのまま使う")
    source: Literal["user", "adopted", "assumed"] = Field(description=_SOURCE_DESCRIPTION)
    points: list[str] = Field(description="根拠の観点のキー(【観点の表】の key。1つ以上)")
    unchanged: bool = Field(default=False, description=UNCHANGED_DESCRIPTION)


class GeneratedUserScene(BaseModel):
    user: str = Field(description="利用者(誰が)")
    scene: str = Field(description="使う場面(いつ・何のために)")
    source: Literal["user", "adopted", "assumed"] = Field(description=_SOURCE_DESCRIPTION)


class RequirementsGenerationOutput(BaseModel):
    """要件定義書の下書きの構造化出力。"""

    purpose: str = Field(description="1.1 プロジェクトの目的・背景(解決したい課題と目指すゴール)")
    users: list[GeneratedUserScene] = Field(description="1.2 ターゲットユーザー・利用シーン")
    in_scope: list[str] = Field(description="1.3 今回開発する範囲(短い句)")
    out_of_scope: list[str] = Field(description="1.3 今回は開発しない範囲(短い句)")
    requirements: list[GeneratedRequirement] = Field(
        description="1.4〜1.6 の要件(機能要件・非機能要件・制約)"
    )


SYSTEM_PROMPT = (
    "あなたは優秀なITコンサルタント・システムアナリストです。【会話】と【観点の表】をもとに、"
    "システム開発の要件定義書の中身を、要件の行として書きます。\n"
    "規則:\n"
    "- 要件は1行に1つ。機能要件(FR)は MoSCoW の優先度を付ける。Won't は今回作らないと"
    "決めたもの\n"
    "- 非機能要件(NFR)は IPA 非機能要求グレードの大項目ごとに、決めたことを書く。"
    "【観点の表】で対象外の観点の要件は書かない\n"
    "- 制約(CON)は、技術スタック・外部サービス・配置・体制・期間・予算など、"
    "設計の前提になるもの\n"
    "- 技術スタック・外部サービス(使う AI・LLM を含む)・配置は、【会話】で利用者が挙げたものを"
    "落とさずに書く。【観点の表】の決めたことは短い要約なので、【会話】の原文から拾う\n"
    "- points には根拠の観点のキーを書く(【観点の表】の key)\n"
    "- source(出どころ)は正直に付ける。利用者が言ったこと・決めたことは user、"
    "AI の提案を利用者が選んだこと(【観点の表】で『AI の提案を採用』の観点)は adopted、"
    "利用者が言っていないのに補ったこと(一般的な前提・具体的な数値・技術の選定など)は"
    " assumed。迷ったら assumed にする\n"
    "- 利用者が言っていない技術スタック・数値を創作しない。必要なら assumed の要件として"
    "書く\n"
    "- セキュリティの観点で、ログインが要る・本人以外に見せないと決めたなら、認証(登録・"
    "ログイン)の機能要件を Must で書く。方法を利用者が言っていなければ、その要件は assumed\n"
    "- 要件ID は書かない(システムが振る)。【前の版の要件】に同じ要件があれば、"
    "その ID を prev_id に書く。中身を言い直しただけの要件も同じ要件として扱う\n"
    f"{REVISION_RULE}(【前の版】は【前の版の要件】)"
)


def points_table(states: Mapping[str, PointState], scale: Scale | None) -> str:
    """プロンプトに埋める観点の表(1行1観点: key|名前|状態|決めたこと)。"""
    lines = [f"規模: {SCALE_LABEL[scale]}" if scale else "規模: 未決"]
    lines += [
        f"{d.key}|{d.label}|{STATUS_LABEL[states[d.key].status]}|{states[d.key].summary or '—'}"
        for d in POINT_DEFS
        if d.key in states
    ]
    return "\n".join(lines)


def previous_rows_text(previous: RequirementsModel | None) -> str:
    """前の版の要件(ID の引き継ぎに使う)。無ければ「なし」。"""
    if previous is None or not previous.rows:
        return NONE_TEXT
    return "\n".join(f"{r.id}|{r.text}" for r in previous.rows)


def build_requirements_messages(
    transcript: str,
    states: Mapping[str, PointState],
    scale: Scale | None,
    previous: RequirementsModel | None,
) -> list[BaseMessage]:
    human = (
        f"## 観点の表\nkey|名前|状態|決めたこと\n{points_table(states, scale)}\n\n"
        f"## 前の版の要件\n{previous_rows_text(previous)}\n\n"
        f"## 会話\n{transcript}"
    )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=human)]


def to_draft(output: RequirementsGenerationOutput) -> RequirementsDraft:
    return RequirementsDraft(
        purpose=output.purpose,
        users=tuple(UserSceneDraft(u.user, u.scene, u.source) for u in output.users),
        in_scope=tuple(output.in_scope),
        out_of_scope=tuple(output.out_of_scope),
        requirements=tuple(_requirement_draft(r) for r in output.requirements),
    )


def _requirement_draft(r: GeneratedRequirement) -> RequirementDraft:
    return RequirementDraft(
        kind=r.kind,
        text=r.text,
        source=r.source,
        points=tuple(r.points),
        priority=r.priority,
        category=r.category,
        prev_id=r.prev_id,
        unchanged=r.unchanged,
    )

