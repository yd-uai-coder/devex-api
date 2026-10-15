"""設計レビューの入力の組み立て(純粋関数)。

入力は、文書の md・ヒアリングの観点の表・決定的な検証の指摘(既知)の3つ。決定的な検証の
指摘は既知として渡し、繰り返させない(レビューは機械で見られないところに使う)。
"""

from collections.abc import Mapping, Sequence
from typing import cast

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.detailed_design.procedure_doc import DESIGN_DOCUMENT_LABELS
from app.detailed_design.stages import STAGES, stage_key
from app.documents.issues import DocumentIssue
from app.hearing import POINT_DEFS, SCALE_LABEL, STATUS_LABEL, PointState, Scale, is_required
from app.review.gates import DETAILED_DESIGN_DOC, DETAILED_PLAN_DOC
from app.review.schemas import LEVEL_LABEL

# 文書の種類は文字列で受ける(知らない種類はそのまま出す)。段階の区切りでは詳細設計書・実装計画書と、
# 段階の検証の指摘("stage:N")も並ぶ
_DOC_LABELS: Mapping[str, str] = {
    **cast(Mapping[str, str], DESIGN_DOCUMENT_LABELS),
    DETAILED_DESIGN_DOC: "詳細設計書",
    DETAILED_PLAN_DOC: "実装計画書(詳細設計)",
    **{stage_key(stage): f"段階{stage}" for stage in STAGES},
}

REVIEW_SYSTEM_PROMPT = """あなたは、AI が生成したシステムの設計書のレビュアーです。
設計書は書き換えず、問題の指摘だけを返します。

## 入力
- 設計書(要件定義書・外部設計書、簡易モードでは内部設計書・実装計画書も。詳細設計モードの
  段階の承認の後は、詳細設計書(機能一覧〜処理ロジック、段階7の後は横断事項も)と実装計画書も)
- ヒアリングの観点の表(観点ごとのキー・状態: ユーザーが決定 / AI の提案を採用 / 未決 /
  対象外 と、その内容)
- 決定的な検証の指摘(システムが ID の結び付きなどを機械的に調べた結果)。
  **これらは既知なので、同じ中身を繰り返さない**

## 見る観点
1. 要件 → 設計の追跡: Must の機能要件と非機能要件(特にセキュリティ・アクセス制御・回数の
   上限・性能)と制約条件(外部サービスの上限など)が、画面・API・データ・処理で満たされているか
2. API の完全さ: 画面の操作ごとに API があるか / どのリソースが対象かを特定できるか /
   認証・認可・エラーの応答がそろっているか
3. 文書の中・文書どうしの矛盾
4. 時間のかかる処理と、失敗したときの扱い
5. データモデルと処理の流れ: 受け取ったデータが使われているか、保存の方針が要件と合うか
6. 実装計画の網羅(実装計画書があるとき): 画面・基盤・デプロイ・非機能要件に当たるタスクがあるか
7. ヒアリング: 「AI の提案を採用」「未決」の観点や、内容が浅い観点で、要件に落ちていない
   事実がないか。「未決」の観点は、必須でなくても、設計や計画を決めるのに要るなら挙げる。
   原因がヒアリングにあるもの(利用者に聞き直さないと決められないもの)は
   cause を hearing にし、point_key に観点の表のキーを入れる
8. 対応の正しさ: 画面・API・タスクの「要件」の欄や付録の対応表が、その要件を実際に満たす
   画面・API・タスクを指しているか(関係の無い要件が結ばれていないか)

## 書かれていないことを見る
観点の内容が浅いまま決まっていても、その観点で普通は決めることが要件に無ければ指摘する。
例: セキュリティが「ログインを入れる」だけなら、誰が誰のデータを見られるか(本人以外の閲覧・
変更の可否)・トークンの扱い。不正利用が「上限を設ける」だけなら、上限の値・単位。
要件に無いことを設計の側の穴(API に認可が無い など)として見つけたときも、要件・ヒアリングで
決まっていないなら、設計の指摘とは別に cause=hearing の指摘を1件立てる(聞き直さないと設計を
直せないため)。

## 書き方
- 指摘ごとに、対象を文書の中の ID や名前で書く
- documents には、その指摘を直すときに書き換える文書を入れる(人が再生成する文書を選ぶ手がかり)。
  対象がモジュール・API パス・テーブルなど設計の中の名前で、要件を変えずに直せるなら要件定義書を
  入れない。要件が抜けているなら要件定義書と、それを受ける下流の文書を入れる
- evidence には、根拠になる文書の箇所をそのまま抜き書きする
- 一般論(「〜を強化すべき」「〜を検討するとよい」)や、MVP の対象外と決めたことへの指摘は書かない
- 同じ問題は1件にまとめる
- 件数を絞らない。上の観点を1つずつ確かめ、軽微なものも含めて見つけたものはすべて挙げる
"""


def build_points_table(states: Mapping[str, PointState], scale: Scale | None) -> str:
    """観点の表を「キー / 観点 / 必須 / 状態 / 内容」の md の表にする(観点の定義の順)。必須の欄は
    必須・任意。"""
    rows = ["| キー | 観点 | 必須 | 状態 | 内容 |", "|---|---|---|---|---|"]
    for d in POINT_DEFS:
        state = states.get(d.key)
        status = STATUS_LABEL[state.status] if state else STATUS_LABEL["open"]
        summary = state.summary if state else ""
        required = "必須" if is_required(d.key, scale) else "任意"
        rows.append(f"| {d.key} | {d.label} | {required} | {status} | {summary} |")
    scale_text = SCALE_LABEL[scale] if scale else "未決"
    return f"規模: {scale_text}\n\n" + "\n".join(rows)


def format_known_issues(issues: Sequence[tuple[str, DocumentIssue]]) -> str:
    """決定的な検証の指摘(文書の種類と指摘の組)を1行ずつにする。無ければ「なし」。"""
    lines = [
        f"- [{_DOC_LABELS.get(doc_type, doc_type)}] {LEVEL_LABEL[i.level]} {i.code}: "
        f"{i.message}" + (f"(対象: {', '.join(i.targets)})" if i.targets else "")
        for doc_type, i in issues
    ]
    return "\n".join(lines) or "- なし"


def build_review_messages(
    documents: Mapping[str, str],
    states: Mapping[str, PointState],
    scale: Scale | None,
    issues: Sequence[tuple[str, DocumentIssue]],
) -> list[BaseMessage]:
    """レビューのメッセージ(指示 + 文書・観点の表・既知の指摘)。`documents`は文書の種類 →
    md(渡した順に並べる)。"""
    combined = "\n\n".join(
        f"## {_DOC_LABELS.get(doc_type, doc_type)}\n{content}"
        for doc_type, content in documents.items()
    )
    human = (
        f"# 設計書\n\n{combined}\n\n# ヒアリングの観点の表\n\n{build_points_table(states, scale)}"
        f"\n\n# 決定的な検証の指摘(既知)\n\n{format_known_issues(issues)}"
    )
    return [SystemMessage(content=REVIEW_SYSTEM_PROMPT), HumanMessage(content=human)]
