from pydantic import BaseModel


class ValidationIssue(BaseModel):
    """バリデーション結果1件分。`element_id`は問題箇所(要素または関係のid)。
    診断8の方針どおり、機械的に検証できるLLM出力の誤りは検出するが、
    重大度(エラー/警告)は呼び出し側(app/uml/validation/__init__.pyのvalidate_diagram)が
    どちらのリストに積むかで表現する。"""

    code: str
    message: str
    element_id: str | None = None


class ValidationResult(BaseModel):
    """`POST .../uml/diagrams/{id}/validate`の応答本体。例外は投げず、常に200でこの形を返す
    (承認可否の判定はPhase 12が`errors`の有無を見て行う。Phase 8時点では結果を返すだけ)。"""

    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    @property
    def is_valid(self) -> bool:
        """承認可能かどうか(エラーが1件も無いか)。警告は承認をブロックしない。"""
        return not self.errors # errorsが空ならTrueを返す
