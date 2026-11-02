from app.models.project import Project
from app.services.errors import DesignStagesNotAvailableError


def require_detailed(project: Project, what: str) -> None:
    """合意済みの版・変更要求・合意の記録は詳細設計モードだけ(作成も読み出しも)。簡易モードなら
    DesignStagesNotAvailableError(409)。画面は、この 409 を受けて「詳細設計モードだけ」と
    知らせる。束・CR・合意のサービスが共有する(合意のサービスは束のサービスから呼ばれるので、
    循環しないように末端のモジュールに置く)。"""
    if project.mode != "detailed":
        raise DesignStagesNotAvailableError(
            f"{what} are not available for project {project.id} (mode={project.mode})"
        )
