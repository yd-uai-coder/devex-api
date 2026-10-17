"""文書の再生成を、前の版の差分にとどめるための入出力(純粋関数)。

再生成で AI が文書を書き直すと、利用者が頼んでいない行も言い換え・並べ替えられ、変更の影響の印が
その分だけ増える(Phase 49 の測定)。そこで、前の版の行を AI に見せ、変える必要の無い行は
「変更なし」(`unchanged`)とだけ答えさせて、組み立て(各文書の`assemble.build_model`)が前の版の
行をそのまま写す。言い回しを AI に任せない(写すのはシステム)ので、変更なしの行は版を重ねても
一字も変わらない。

- AI への手がかりは、前の版の行(`rows_text`)と、変更のきっかけの節。きっかけは文書ごとに違い
  (外部・内部・計画は上流の文書で変わった行(`changed_rows`)と、設計レビューの採用した指摘から
  組み立てた修正指示(`instruction_text`。app/review/sendback.py)。要件定義書は観点の表・修正指示・
  会話)、各文書の drafting が`RevisionInputs`で、プロンプトの節を「前の版・きっかけ・参照」に
  分けて持つ。規則の文(`revision_rule`)はそこから組み立てる。新しい入力の節を足したら、
  どれに分けるかを決めないとテストが落ちる(きっかけの一覧の直し忘れを防ぐ。Phase 59 で、
  観点の表の変化がきっかけに入っていなかった)。
- 行の照合のキーは各文書の安定した ID か名前(要件ID・画面ID・API・テーブル名・モジュールの
  パス・処理別データフローのトリガー・作業単位のキー)。
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from app.documents.external_design.model import ExternalDesignModel
from app.documents.internal_design.model import InternalDesignModel
from app.documents.requirements.model import RequirementsModel

NONE_TEXT = "なし"


@dataclass(frozen=True)
class RevisionInputs:
    """再生成のプロンプトの節(`## 名前`)の分け方。`previous`は前の版の行、`triggers`は変更の
    きっかけ(節の名前 → 何が変わったら書き直すか)、`context`は読むだけの節(きっかけにしない)。"""

    previous: tuple[str, ...]
    triggers: Mapping[str, str]
    context: tuple[str, ...] = ()

    @property
    def sections(self) -> set[str]:
        return {*self.previous, *self.triggers, *self.context}


def _names(sections: Iterable[str]) -> str:
    return "".join(f"【{name}】" for name in sections)


def revision_rule(inputs: RevisionInputs) -> str:
    """再生成の規則の文。きっかけの節に関係しない行は前の版のまま(`unchanged`)にさせる。"""
    triggers = "・".join(f"【{name}】({when})" for name, when in inputs.triggers.items())
    context = (
        f"{_names(inputs.context)}は読むだけで、それ自体はきっかけにしない。"
        if inputs.context
        else ""
    )
    return (
        f"- {_names(inputs.previous)}(前の版)があるときは、前の版を直す再生成。変更のきっかけは"
        f"{triggers}。どのきっかけにも関係しない行は、前の版の同じ行を指して unchanged を true に"
        "し、言い換え・並べ替え・項目名の変更をしない(unchanged の行は、システムが前の版の行を"
        f"そのまま使う)。関係する行だけを書き直す。{context}前の版が「なし」なら unchanged は false"
    )


UNCHANGED_DESCRIPTION = (
    "【前の版】の同じ行を変えないなら true(システムが前の版の行をそのまま使う)。"
    "書き直す行・新しい行は false"
)


def rows_text(rows: Iterable[BaseModel]) -> str:
    """前の版の行(1行1つの JSON)。無ければ「なし」。"""
    lines = [json.dumps(row.model_dump(mode="json"), ensure_ascii=False) for row in rows]
    return "\n".join(lines) or NONE_TEXT


def changed_rows(before: Mapping[str, BaseModel], after: Mapping[str, BaseModel]) -> list[str]:
    """前の版から変わった行(今回の版の順。消えた行は最後)。前の版が無ければ空(初回の生成)。"""
    if not before:
        return []
    changed = [
        f"{key}(新しい)" if key not in before else f"{key}(変わった)"
        for key, row in after.items()
        if key not in before or before[key].model_dump() != row.model_dump()
    ]
    return changed + [f"{key}(消えた)" for key in before if key not in after]


def changed_rows_text(changes: Sequence[str]) -> str:
    return "\n".join(f"- {c}" for c in changes) or NONE_TEXT


def instruction_text(instruction: str) -> str:
    """プロンプトの【修正指示】の節。無ければ「なし」。"""
    return instruction.strip() or NONE_TEXT


def requirement_rows(model: RequirementsModel | None) -> dict[str, BaseModel]:
    return {f"要件 {row.id}": row for row in model.rows} if model else {}


def external_design_rows(model: ExternalDesignModel | None) -> dict[str, BaseModel]:
    if model is None:
        return {}
    return {
        **{f"画面 {s.id}": s for s in model.screens},
        **{f"API {a.label}": a for a in model.apis},
    }


def internal_design_rows(model: InternalDesignModel | None) -> dict[str, BaseModel]:
    if model is None:
        return {}
    return {
        **{f"テーブル {t.name}": t for t in model.tables},
        **{f"モジュール {m.path}": m for m in model.modules},
        **{f"処理別データフロー {d.id}({d.trigger})": d for d in model.dataflows},
    }
