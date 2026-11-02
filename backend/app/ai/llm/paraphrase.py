"""名称を決定的に言い換える規則(純粋関数)。

実 LLM は再生成のたびに、外部連携のサービス名・利用者の名前・処理の名称を少し言い換えることがある
(「X API」→「X」、「トップページ表示」→「トップページ表示処理」)。名前で同一性を決める照合
(合意の保護・段階1 の ID の引き継ぎ・確認事項のキー)がこの揺れに耐えるかを、偽の LLM で確かめる
ために、同じ揺れを決定的に作る。

- 単体テストは、生成の構造化出力をここの関数で言い換えて、再生成の出力として使う。
- E2E の偽の LLM(app/ai/llm/fake.py)は、会話に`PARAPHRASE_MARK`があるプロジェクトだけ、
  再生成の出力を言い換える(印の無いプロジェクトの出力は変えない)。
"""

import re

from app.detailed_design.drafting import FunctionListGenerationOutput
from app.documents.external_design.drafting import ExternalDesignGenerationOutput
from app.documents.requirements.drafting import RequirementsGenerationOutput

# E2E の偽の LLM に言い換えさせるプロジェクトの印(プロジェクトの概要などに書く)
PARAPHRASE_MARK = "[言い換え]"
# 名前の後ろに足す語
PARAPHRASE_SUFFIX = "(言い換え)"

# サービス名の末尾の「API」(前の空白も含める)
_SERVICE_API = re.compile(r"\s*API$")


def paraphrase_name(name: str) -> str:
    """名前の後ろに`PARAPHRASE_SUFFIX`を足す(すでに付いていれば足さない)。"""
    name = name.strip()
    return name if name.endswith(PARAPHRASE_SUFFIX) else f"{name}{PARAPHRASE_SUFFIX}"


def base_name(name: str) -> str:
    """`paraphrase_name`で足した語を外した名前。"""
    return name.strip().removesuffix(PARAPHRASE_SUFFIX)


def paraphrase_service(service: str) -> str:
    """外部連携のサービス名の言い換え。末尾の「API」があれば外し、無ければ「 API」を足す。"""
    service = service.strip()
    if _SERVICE_API.search(service):
        return _SERVICE_API.sub("", service)
    return f"{service} API"


def paraphrase_requirements(output: RequirementsGenerationOutput) -> RequirementsGenerationOutput:
    """要件定義書の下書きの、利用者(1.2)の名前を言い換える(要件の行は変えない。要件は前の版の
    ID を`prev_id`で指すので、文の言い換えには元から耐える)。"""
    users = [u.model_copy(update={"user": paraphrase_name(u.user)}) for u in output.users]
    return output.model_copy(update={"users": users})


def paraphrase_external_design(
    output: ExternalDesignGenerationOutput,
) -> ExternalDesignGenerationOutput:
    """外部設計書の下書きの、画面名と外部連携のサービス名を言い換える(画面ID・API は変えない)。"""
    screens = [s.model_copy(update={"name": paraphrase_name(s.name)}) for s in output.screens]
    integrations = [
        i.model_copy(update={"service": paraphrase_service(i.service)})
        for i in output.integrations
    ]
    return output.model_copy(update={"screens": screens, "integrations": integrations})


def paraphrase_function_list(output: FunctionListGenerationOutput) -> FunctionListGenerationOutput:
    """段階1 の下書きの、処理の名称を言い換える(種別・トリガー・関連画面は変えない)。"""
    functions = [f.model_copy(update={"name": paraphrase_name(f.name)}) for f in output.functions]
    return output.model_copy(update={"functions": functions})
