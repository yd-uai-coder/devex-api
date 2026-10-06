"""unique (project_id, doc_type, version) on generated_documents

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-10-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 二重実行で同じ版の番号が既に重複していると、制約を張れない。どの行を残すかは内容を見て
    # 人が決めることなので、自動では消さずに止める(重複の組を表示する)。
    duplicates = op.get_bind().execute(
        sa.text(
            """
            SELECT project_id, doc_type, version, COUNT(*) AS n
            FROM generated_documents
            GROUP BY project_id, doc_type, version
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if duplicates:
        listed = ", ".join(f"({r.project_id}, {r.doc_type}, v{r.version}) x{r.n}" for r in duplicates)
        raise RuntimeError(
            "generated_documents に同じ版の番号の行が重複しています。"
            f"重複を解消してから再実行してください: {listed}"
        )
    op.create_unique_constraint(
        'uq_generated_documents_project_type_version',
        'generated_documents',
        ['project_id', 'doc_type', 'version'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_generated_documents_project_type_version', 'generated_documents', type_='unique')
