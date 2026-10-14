"""設計レビューを使うかどうかの判定(純粋関数)。"""


def review_effective(*, is_superuser: bool, review_enabled: bool) -> bool:
    """レビューの実効値。superuser が自分でオンにしたときだけ真(初期値はオフ)。superuser で
    ないアカウントは、保存された設定によらずオフ(意図しない Anthropic の課金を防ぐ)。"""
    return is_superuser and review_enabled
