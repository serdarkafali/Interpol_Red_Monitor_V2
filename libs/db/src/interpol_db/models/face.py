"""Faces found on notice photos and their embeddings for similarity search."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from interpol_db.base import Base

if TYPE_CHECKING:
    from interpol_db.models.notice_photo import NoticePhoto

# Length of one SFace embedding.
EMBEDDING_DIMENSIONS = 128


class Face(Base):
    """One face detected on a photo: its box, detection score and embedding."""

    __tablename__ = "faces"
    __table_args__ = (
        CheckConstraint("box_width > 0 AND box_height > 0", name="box_size_positive"),
        CheckConstraint(
            "detection_score >= 0 AND detection_score <= 1", name="detection_score_range"
        ),
        # HNSW index for fast nearest-neighbour search by cosine distance.
        Index(
            None,
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    photo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("notice_photos.id", ondelete="CASCADE"), index=True
    )
    # Face box in pixels on the original photo.
    box_x: Mapped[int] = mapped_column(Integer)
    box_y: Mapped[int] = mapped_column(Integer)
    box_width: Mapped[int] = mapped_column(Integer)
    box_height: Mapped[int] = mapped_column(Integer)
    detection_score: Mapped[float] = mapped_column(Float)
    # L2-normalised SFace embedding.
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Name of the model that produced the embedding, e.g. "sface-2021dec".
    model: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    photo: Mapped[NoticePhoto] = relationship(back_populates="faces")
