from pydantic import BaseModel


class DataItemField(BaseModel):
    """データ項目(DataItem)1件が持つフィールド1個分の定義。

    型・必須は任意項目として持たせるだけにとどめる。データ辞書の骨格は
    「名前+フィールド名の一覧」。
    """

    name: str
    type: str | None = None
    required: bool | None = None
