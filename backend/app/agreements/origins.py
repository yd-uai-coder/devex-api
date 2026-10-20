"""合意の出どころ(合意済みの版・「採用にする」・CR)と、画面に出す名前(純粋関数)。

docs/external_design.md 2.15節。出どころは3つ。

- `baseline`: 合意済みの版(束)を作ったとき。入った要素をまとめて合意にし、今の合意にあって束に
  無い要素は「外れた」と記録する。
- `adopt`: 推測の要件を「採用にする」で確かめたとき(その行だけ)。
- `change_request`: CR を反映したとき(対象の要件と、予測が届いた画面・API。Phase 93)。
"""

from typing import Literal, get_args

AgreementOrigin = Literal["baseline", "adopt", "change_request"]

AGREEMENT_ORIGINS: tuple[AgreementOrigin, ...] = get_args(AgreementOrigin)
ADOPT_LABEL = "採用にする"


def origin_label(
    origin: AgreementOrigin, *, baseline_number: int | None = None, cr_number: int | None = None
) -> str:
    """出どころの名前(`B-1`・`採用にする`・`CR-1`)。番号が無い(束・CR の行が無い)ときは
    種類の名前だけにする。"""
    if origin == "baseline":
        return f"B-{baseline_number}" if baseline_number is not None else "合意済みの版"
    if origin == "change_request":
        return f"CR-{cr_number}" if cr_number is not None else "CR"
    return ADOPT_LABEL
