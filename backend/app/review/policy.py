"""設計レビューを使うかどうかの判定(純粋関数)。"""


def review_allowed(*, is_superuser: bool, has_own_review_key: bool) -> bool:
    """レビューを使えるアカウントか。superuser(既定のキーで使える)か、レビューの用途の AI の
    自分のキーを登録している(費用は本人のキーにかかる)とき。ほかは既定のキーで意図しない
    Anthropic の課金が起きないよう、使えない。"""
    return is_superuser or has_own_review_key


def review_effective(*, review_allowed: bool, review_enabled: bool) -> bool:
    """レビューの実効値。使えるアカウントが自分でオンにしたときだけ真(初期値はオフ)。使えない
    アカウントは、保存された設定によらずオフ。"""
    return review_allowed and review_enabled
