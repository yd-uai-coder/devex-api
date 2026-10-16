"""マイページで選べる LLM の表(provider → モデル)と、用途の定義。

公式の一覧で確かめた(2026-10-16。docs/external_design.md 2.12節)。終了の告知・新しいモデルは
この表を直して足す。純粋なデータと関数だけを置き、DB・設定には触れない。
"""

from dataclasses import dataclass
from enum import StrEnum


class Provider(StrEnum):
    """API キーを登録する単位(AI の提供元)。"""

    GEMINI = "gemini"
    CLAUDE = "claude"


class Purpose(StrEnum):
    """モデルを選ぶ用途。参考資料(PDF)の取り込みは用途にせず、Gemini で読む。"""

    CHAT = "chat"
    GENERATION = "generation"
    REVIEW = "review"
    JUDGE = "judge"


@dataclass(frozen=True)
class CatalogModel:
    """選べるモデル1つ。`note` は画面に出す短い注記(費用・特徴)。`timeout_seconds` は1回の呼び出しを
    待つ上限(応答の速さはモデルで大きく違うので、モデルごとに持つ)。"""

    provider: Provider
    model_id: str
    label: str
    note: str
    timeout_seconds: float


@dataclass(frozen=True)
class PurposeInfo:
    """用途の表示名と説明。`recommend_low_cost` は「呼ぶ回数が多いので費用の低いモデルを推奨」の
    注記を出す用途(注記の文言は画面が持つ)。"""

    purpose: Purpose
    label: str
    description: str
    recommend_low_cost: bool = False


PROVIDER_LABELS: dict[Provider, str] = {Provider.GEMINI: "Gemini", Provider.CLAUDE: "Claude"}

# 価格は 100万トークンあたりの入力 / 出力。タイムアウトは実測の最長に余裕を見た値
# (Flash-Lite は生成の1回が最大約11秒、3.8 Flash は最大約176秒、Sonnet のレビューは 25〜43 秒)
CATALOG: tuple[CatalogModel, ...] = (
    CatalogModel(
        Provider.GEMINI,
        "gemini-3.8-flash",
        "Gemini 3.8 Flash",
        "Flash の最新。無料枠は1日20回",
        240.0,
    ),
    CatalogModel(
        Provider.GEMINI, "gemini-3.6-flash", "Gemini 3.6 Flash", "Flash", 120.0
    ),
    CatalogModel(
        Provider.GEMINI, "gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "Flash-Lite の最新", 30.0
    ),
    CatalogModel(
        Provider.GEMINI,
        "gemini-3.1-flash-lite",
        "Gemini 3.1 Flash-Lite",
        "2027-05-07 以降に終了",
        30.0,
    ),
    CatalogModel(
        Provider.CLAUDE, "claude-haiku-5-5", "Claude Haiku 5.5", "低費用($0.10 / $0.50)", 60.0
    ),
    CatalogModel(
        Provider.CLAUDE, "claude-sonnet-5-5", "Claude Sonnet 5.5", "標準($2 / $10)", 180.0
    ),
    CatalogModel(
        Provider.CLAUDE, "claude-opus-5-5", "Claude Opus 5.5", "高性能($4 / $20)", 240.0
    ),
    CatalogModel(
        Provider.CLAUDE,
        "claude-fable-5-1",
        "Claude Fable 5.1",
        "最上位($10 / $50)。1回の呼び出しが長い",
        300.0,
    ),
)

PURPOSES: tuple[PurposeInfo, ...] = (
    PurposeInfo(Purpose.CHAT, "チャット", "ヒアリングのチャットの応答と、観点の判定"),
    PurposeInfo(Purpose.GENERATION, "生成", "設計書・詳細設計の段階・実装手順書の生成"),
    PurposeInfo(Purpose.REVIEW, "レビュー", "生成した設計書の設計レビュー(指摘だけを返す)"),
    PurposeInfo(
        Purpose.JUDGE,
        "判定",
        "レビューで採用した指摘が、再生成で直ったかの確認",
        recommend_low_cost=True,
    ),
)


def find_model(provider: Provider, model_id: str) -> CatalogModel | None:
    """provider とモデルの組が表にあればそれを、無ければ None を返す。"""
    return next(
        (m for m in CATALOG if m.provider == provider and m.model_id == model_id), None
    )


def provider_of(model_id: str) -> Provider | None:
    """モデル名からその provider を返す(表に無ければ None)。seed が `.env` のモデルに使う。"""
    return next((m.provider for m in CATALOG if m.model_id == model_id), None)
