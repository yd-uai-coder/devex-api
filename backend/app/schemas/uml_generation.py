import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.uml.domain import NotationType


class UmlSubjectSpec(BaseModel):
    """生成対象1件の指定。`subject`はDFDなら処理名(候補一覧の`title`)、ERの部分図ならグループ名、
    component/ERの全体なら''。`tables`はER部分図で対象にするテーブル名(再生成で省略すると
    前回の選択を再利用する)。"""

    subject: str = Field(default="", max_length=255)
    tables: list[str] | None = None


class UmlGenerateRequest(BaseModel):
    """UML図のAI生成リクエスト。`subjects`は個別生成なら1件、一括生成なら複数件
    (上限はapp/services/uml_generation_service.pyのMAX_SUBJECTS_PER_REQUEST)。
    component/ERの全体生成では省略してよい。"""

    notation: NotationType
    subjects: list[UmlSubjectSpec] = []


class DfdSubjectRead(BaseModel):
    """DFDの生成対象の候補1件(内部設計書の`#### DF-<n>: <処理名>`見出し)。"""

    code: str
    title: str


class UmlCandidatesRead(BaseModel):
    """内部設計書から列挙した生成対象の候補。内部設計書が無い場合は`internal_design_version`が
    Noneで、候補は空。内部設計書がPhase 10以前の形式の場合も候補は空になる(再生成を促す)。"""

    internal_design_version: int | None
    dfd_subjects: list[DfdSubjectRead]
    er_tables: list[str]


class UmlGenerationResultRead(BaseModel):
    """生成履歴の中の、対象1件分の結果。`message`は止まった理由と再度の生成指示が必要な旨。"""

    subject: str
    diagram_id: uuid.UUID
    outcome: Literal["succeeded", "failed", "skipped"]
    reason_code: str | None
    message: str | None


class UmlGenerationRunRead(BaseModel):
    """UML図のAI生成リクエスト1回分の履歴。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    notation: NotationType
    status: str
    requested: list[dict]
    results: list[UmlGenerationResultRead]
    started_at: datetime
    finished_at: datetime | None
