"""合意した変更要求(CR)から、再生成の指示の文を組み立てる(純粋関数)。

docs/external_design.md 2.14節「反映」。設計レビューの採用した指摘(app/review/sendback.py)と
同じ経路で、文書・段階の生成のプロンプトの【修正指示】の節に入れる。CR は要件が起点なので、
文書・段階ごとに絞らず、どの生成にも同じ指示を渡す。指摘の指示と一緒に渡すときは、空行で並べる。
"""

from collections.abc import Iterable

from app.change_requests.explanation import ChangeRequestFacts
from app.change_requests.options import option_label
from app.change_requests.targets import TARGET_KIND_LABEL

CR_INSTRUCTION_HEAD = (
    "次の合意済みの変更要求(CR-{number})を反映して書き直す。対象の要件と、それに合わせて変える"
    "必要のある行だけを書き直し、変更要求に関係しない行は変えない(unchanged にする)。"
)
ADDS_REQUIREMENT_LINE = "- 新しい要件を足す(内容は上の「内容」のとおり。ID は既存の要件と重ねない)"
ORIGIN_LINE = "  - 新しい要件が関わる既存の要素(予測の起点): {origins}"
OPTION_LINE = "合意した案: {label} {name}(結果: {outcome})"


def build_cr_instruction(facts: ChangeRequestFacts) -> str:
    """指示の文(頭の1文 + タイトル・内容 + 合意した案があればその1行 + 対象1件1行 + 新しい要件を
    足すなら1行と、起点があればその1行)。案のある CR では、合意で選んだ案の対象が CR の対象に
    なっている。"""
    lines = [
        CR_INSTRUCTION_HEAD.format(number=facts.number),
        f"タイトル: {facts.title}",
        f"内容: {facts.content.strip() or '—'}",
    ]
    number = facts.decision_option
    if number is not None and 1 <= number <= len(facts.options.items):
        option = facts.options.option(number)
        lines.append(
            OPTION_LINE.format(
                label=option_label(number),
                name=option.name,
                outcome=" ".join(option.outcome.split()),
            )
        )
    if facts.targets:
        lines.append("対象の要件:")
    for target in facts.targets:
        kind = TARGET_KIND_LABEL[target.kind]
        if target.kind == "delete":
            lines.append(f"- {target.requirement_id}({kind}): この要件を消す")
        elif target.proposed_text:
            lines.append(
                f"- {target.requirement_id}({kind}): 変更後の案「{target.proposed_text}」"
            )
        else:
            lines.append(f"- {target.requirement_id}({kind}): 上の「内容」のとおりに変える")
    if facts.adds_requirement:
        lines.append(ADDS_REQUIREMENT_LINE)
        if facts.origin_labels:
            lines.append(ORIGIN_LINE.format(origins="・".join(facts.origin_labels)))
    return "\n".join(lines)


def join_instructions(instructions: Iterable[str]) -> str:
    """指示の文を空行で並べる(空の指示は飛ばす)。どれも空なら空。"""
    return "\n\n".join(text for text in instructions if text)
