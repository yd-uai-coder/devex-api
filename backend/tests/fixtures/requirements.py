"""要件定義書の構造化出力の見本(生成のテストで偽の LLM に返させる)。"""

from app.documents.requirements.drafting import (
    GeneratedRequirement,
    GeneratedUserScene,
    RequirementsGenerationOutput,
)


def requirements_output(
    *, prev_id: str = "", text: str = "備品を予約できる"
) -> RequirementsGenerationOutput:
    """FR(コア機能)1行と NFR(セキュリティ)1行。出どころはどちらも user と書く(観点の状態で
    上限がかかるかを確かめるため)。"""
    return RequirementsGenerationOutput(
        purpose="内容",
        users=[GeneratedUserScene(user="社員", scene="備品を借りるとき", source="user")],
        in_scope=["予約"],
        out_of_scope=["決済"],
        requirements=[
            GeneratedRequirement(
                kind="FR",
                prev_id=prev_id,
                priority="Must",
                text=text,
                source="user",
                points=["core"],
            ),
            GeneratedRequirement(
                kind="NFR",
                category="セキュリティ",
                text="HTTPS",
                source="user",
                points=["security"],
            ),
        ],
    )
