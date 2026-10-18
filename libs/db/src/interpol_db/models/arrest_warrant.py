"""Arrest warrants attached to a red notice."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CHAR, BigInteger, ForeignKey, Identity, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from interpol_db.base import Base

if TYPE_CHECKING:
    from interpol_db.models.country import Country
    from interpol_db.models.notice import Notice


class ArrestWarrant(Base):
    """A warrant behind a notice; a notice can have several, from different countries."""

    __tablename__ = "arrest_warrants"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    notice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("notices.id", ondelete="CASCADE"), index=True
    )
    # Not derived from the person's nationalities: the issuing country often differs.
    issuing_country_code: Mapped[str] = mapped_column(
        CHAR(2), ForeignKey("countries.code"), index=True
    )
    charge: Mapped[str] = mapped_column(Text)
    charge_translation: Mapped[str | None] = mapped_column(Text)

    notice: Mapped[Notice] = relationship(back_populates="arrest_warrants")
    issuing_country: Mapped[Country] = relationship()
