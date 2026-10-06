"""日本語文字幅推定と折り返し。

出自: 別プロジェクトの自作図生成エンジンから`wrap`/`tw`/`cw`をそのまま移植
(生成文書・図ラベルは日本語なので、日本語の文字幅推定が要る)。
"""

import re
import unicodedata

FONT = 14  # ノードラベルの文字サイズ(px)。これ未満にしない


def cw(ch: str, size: float) -> float:
    """1文字の描画幅の見積もり(実フォントが無くても働くよう保守的に)。"""
    ea = unicodedata.east_asian_width(ch)
    if ea in ("W", "F"):
        return size
    if ea == "A":  # ① → × ≤ など。CJKフォントでは全角で描かれる
        return size if ord(ch) > 0x2000 else size * 0.6
    if ch == " ":
        return size * 0.32
    if ch in ".,:;|!'`()[]{}/\\-_":
        return size * 0.44
    if ch.isupper() or ch.isdigit():
        return size * 0.66
    return size * 0.6


def tw(s: str, size: float = FONT) -> float:
    return sum(cw(c, size) for c in s) * 1.05


_KINSOKU = set("、。,.)）」』:;!?")  # 行頭に置かない文字
_SPLIT_AT = re.compile(r"(?<=[_./(,:=])")


def _tokens(para: str) -> list[str]:
    """ASCIIの連続(識別子・URL等)は1単語、それ以外(全角・空白)は1文字ずつ。"""
    return re.findall(r"[\x21-\x7e]+|.", para)


def wrap(text: str, maxw: float, size: float = FONT, hard: float | None = None) -> list[str]:
    """maxwに収まるよう折り返す。明示的な改行(\\n)は尊重し、識別子は極力壊さない。
    最終行が1〜2文字だけになる場合は、前の行に寄せる(少しはみ出してもよい)。"""
    out: list[str] = []
    for para in text.split("\n"):
        pieces: list[str] = []
        for tok in _tokens(para):
            if tw(tok, size) > (hard or maxw):  # 長すぎる識別子だけ、_ . / ( , の直後で割る
                pieces.extend(p for p in _SPLIT_AT.split(tok) if p)
            else:
                pieces.append(tok)
        lines: list[str] = []
        cur = ""
        for pc in pieces:
            if (
                cur
                and tw(cur + pc, size) > maxw
                and not (pc[0] in _KINSOKU and tw(cur + pc, size) <= maxw * 1.12)
            ):
                lines.append(cur.rstrip())
                cur = pc.lstrip()
            else:
                cur += pc
        lines.append(cur.rstrip())
        # 孤立した短い最終行を前の行へ寄せる
        orphan_short = len(lines) >= 2 and len(lines[-1]) <= 2
        if orphan_short and tw(lines[-2] + lines[-1], size) <= maxw * 1.2:
            lines[-2] += lines[-1]
            lines.pop()
        out.extend(lines)
    return [line for line in out if line != ""] or [""]
