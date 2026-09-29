from pydantic import BaseModel


class DataItemField(BaseModel):
    """データ項目(DataItem)1件が持つフィールド1個分の定義。

    型・必須は現時点では任意項目として持たせるだけにとどめる
    (appendix/stage3-requirements-organization.md 診断8品質指標: 「データ項目の型と必須は、
    現時点ではスキーマに任意項目として持たせるだけにする」)。M2bの決定である
    「名前+フィールド名の一覧」というデータ辞書の骨格自体は変えない。
    """

    name: str
    type: str | None = None
    required: bool | None = None
