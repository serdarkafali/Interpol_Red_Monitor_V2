"""Red notices and their nationalities."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from interpol_db.base import Base

if TYPE_CHECKING:
    from interpol_db.models.arrest_warrant import ArrestWarrant
    from interpol_db.models.country import Country
    from interpol_db.models.notice_photo import NoticePhoto

# Many-to-many link between a notice and the countries of its nationalities. It has no
# columns of its own, so a plain table is enough; no ORM class is needed.
notice_nationalities = Table(
    "notice_nationalities",
    Base.metadata,
    Column(
        "notice_id",
        BigInteger,
        ForeignKey("notices.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("country_code", CHAR(2), ForeignKey("countries.code"), primary_key=True),
    # The primary key index starts with notice_id; country_code needs its own index.
    Index(None, "country_code"),
)


class Notice(Base):
    """One Interpol red notice, the person it describes and its lifecycle timestamps."""

    __tablename__ = "notices"
    __table_args__ = (
        CheckConstraint("sex_id IN ('M', 'F', 'U')", name="sex_id_valid"),
        CheckConstraint("height > 0 AND height < 3", name="height_range"),
        CheckConstraint("weight > 0 AND weight < 500", name="weight_range"),
        # Used by the retention job to find soft-deleted notices older than 50 days.
        Index(None, "deleted_at", postgresql_where=text("deleted_at IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    entity_id: Mapped[str] = mapped_column(Text, unique=True)

    name: Mapped[str] = mapped_column(Text)
    forename: Mapped[str | None] = mapped_column(Text)
    sex_id: Mapped[str | None] = mapped_column(Text)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    place_of_birth: Mapped[str | None] = mapped_column(Text)
    country_of_birth_code: Mapped[str | None] = mapped_column(
        CHAR(2), ForeignKey("countries.code"), index=True
    )
    height: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    weight: Mapped[int | None] = mapped_column(SmallInteger)
    distinguishing_marks: Mapped[str | None] = mapped_column(Text)
    # Interpol-specific code lists, kept for display only (see ADR 0004).
    language_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    eye_color_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    hair_color_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    # The thumbnail is a separate image and never one of the notice's photos.
    thumbnail_picture_id: Mapped[str | None] = mapped_column(Text)

    # SHA-256 of the received payload; an unchanged hash means nothing to update.
    content_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Kept current by a database trigger, not by application code.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    country_of_birth: Mapped[Country | None] = relationship()
    nationalities: Mapped[list[Country]] = relationship(secondary=notice_nationalities)
    arrest_warrants: Mapped[list[ArrestWarrant]] = relationship(
        back_populates="notice", cascade="all, delete-orphan", passive_deletes=True
    )
    photos: Mapped[list[NoticePhoto]] = relationship(
        back_populates="notice", cascade="all, delete-orphan", passive_deletes=True
    )
