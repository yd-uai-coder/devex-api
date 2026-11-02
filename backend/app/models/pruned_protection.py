import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, PortableJSON


class PrunedProtection(Base):
    """巡回で消えた版の、合意の形へ戻した記録(docs/internal_design.md 3.2節㉕)。

    版の巡回(app/repositories/generated_document.py の create_version)で消す版が`protection`を
    持っていれば、消す前にここへ写す。要素の履歴(AgreementService.history)が、残った版の
    `protection`と合わせて「合意の形へ戻した」を出すため。巡回で消えるのは CR を反映していない版
    だけなので、CR の番号は持たない。`created_at`は消えた版を作った日時(履歴の並びに使う)。
    この表を足す前に消えた版の分は無い。
    """

    __tablename__ = "pruned_protections"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "doc_type", "version", name="uq_pruned_protections_project_type_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    protection: Mapped[list[dict]] = mapped_column(PortableJSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pruned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
