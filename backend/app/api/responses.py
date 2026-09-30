"""ルートで共有するレスポンスの組み立て部品(ファイルのダウンロード用ヘッダーなど)。"""

from urllib.parse import quote


def content_disposition(filename: str) -> str:
    """日本語等の非ASCII文字を含むファイル名用のContent-Disposition値を組み立てる(RFC 5987)。
    ASCII非対応のクライアント向けにfilename(置換フォールバック)とfilename*(UTF-8)の両方を含める。
    文書のダウンロード(`routes/projects.py`)とUML図の出力(`routes/uml.py`)で共有する。"""
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
