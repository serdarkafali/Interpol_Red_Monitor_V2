"""Automatic comparisons between two faces, written by the face worker (ADR 0012)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from interpol_db.base import Base


class FaceMatch(Base):
    """The cosine distance between two faces.

    ``same_notice`` true: two faces of one notice, kept whatever the distance, to check
    that the photos of a notice show the same person. False: faces of two notices, kept
    only below the match threshold, as a possible match for human review.
    Each pair is stored once, with the smaller face id first.
    """

    __tablename__ = "face_matches"
    __table_args__ = (
        CheckConstraint("first_face_id < second_face_id", name="pair_ordered"),
        UniqueConstraint("first_face_id", "second_face_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # The unique constraint above also serves lookups by first_face_id.
    first_face_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("faces.id", ondelete="CASCADE")
    )
    second_face_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("faces.id", ondelete="CASCADE"), index=True
    )
    # 0: same direction; the threshold is MAX_MATCH_DISTANCE in the face worker.
    distance: Mapped[float] = mapped_column(Float)
    same_notice: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
