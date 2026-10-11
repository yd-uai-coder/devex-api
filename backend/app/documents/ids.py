"""上流の文書の行の ID(`FR-001`・`SCR-001`)を、再生成で引き継ぐ採番(純粋関数)。

AI には ID を決めさせず、前の版の同じ行の ID を指させるだけにする。前の版に在る同じ種別の ID
だけを引き継ぎ、それ以外は種別ごとの次の番号を振る。消えた ID は使い回さない(後続の文書や
段階が ID で行を指すため)ので、種別ごとの次の番号を model に持つ。
"""

from collections.abc import Iterable, Mapping, Sequence


def seq_id(prefix: str, seq: int) -> str:
    return f"{prefix}-{seq:03d}"


def parse_seq_id(value: str, prefixes: Iterable[str]) -> tuple[str, int] | None:
    """`FR-001`を(種別, 番号)にする。種別が`prefixes`に無い・形が違えば None。"""
    prefix, sep, number = value.strip().partition("-")
    if not sep or prefix not in tuple(prefixes) or not number.isdigit():
        return None
    return prefix, int(number)


def initial_next_seq(
    prefixes: Sequence[str], previous_ids: Iterable[str], stored: Mapping[str, int]
) -> dict[str, int]:
    """前の版の次の番号(持っていなければ前の版の行の最大番号 + 1)。"""
    next_seq: dict[str, int] = dict.fromkeys(prefixes, 1)
    for value in previous_ids:
        parsed = parse_seq_id(value, prefixes)
        if parsed is not None:
            prefix, seq = parsed
            next_seq[prefix] = max(next_seq[prefix], seq + 1)
    for prefix, seq in stored.items():
        if prefix in next_seq:
            next_seq[prefix] = max(next_seq[prefix], seq)
    return next_seq


def carry_over_ids(
    kinds_and_prev: Sequence[tuple[str, str]],
    previous_kinds: Mapping[str, str],
    next_seq: Mapping[str, int],
) -> tuple[list[str], dict[str, int]]:
    """行ごとの ID と、次の番号を決める。

    `prev_id`が前の版に在り(`previous_kinds`: ID → 種別)、種別が同じで、まだ使われていなければ
    引き継ぐ。それ以外(新しい行・前の版に無い ID・種別違い・重複)は種別ごとの次の番号を振る。"""
    seq = dict(next_seq)
    used: set[str] = set()
    ids: list[str] = []
    for kind, prev_id in kinds_and_prev:
        prev_id = prev_id.strip().upper()
        if previous_kinds.get(prev_id) == kind and prev_id not in used:
            ids.append(prev_id)
        else:
            ids.append(seq_id(kind, seq[kind]))
            seq[kind] += 1
        used.add(ids[-1])
    return ids, seq
