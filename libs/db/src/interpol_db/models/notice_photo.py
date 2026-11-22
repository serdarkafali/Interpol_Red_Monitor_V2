"""Full-size photos of a red notice and where their files are stored."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, SmallInteger, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from interpol_db.base import Base

if TYPE_CHECKING:
    from interpol_db.models.notice import Notice


class NoticePhoto(Base):
    """One photo of a notice; the file fields stay empty until it is downloaded."""

    __tablename__ = "notice_photos"
    __table_args__ = (
        # Used by the face worker to pick the next photo that is not downloaded yet.
        Index(None, "last_attempt_at", postgresql_where=text("downloaded_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    notice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("notices.id", ondelete="CASCADE"), index=True
    )
    picture_id: Mapped[str] = mapped_column(Text, unique=True)
    # Location of the file in the object store and its SHA-256, set by the face worker.
    object_key: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    download_attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    notice: Mapped[Notice] = relationship(back_populates="photos")
