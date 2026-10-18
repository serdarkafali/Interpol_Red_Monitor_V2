"""Reference table of ISO 3166-1 alpha-2 country codes."""

from sqlalchemy import CHAR, Text
from sqlalchemy.orm import Mapped, mapped_column

from interpol_db.base import Base


class Country(Base):
    """A country or territory, identified by its ISO 3166-1 alpha-2 code.

    The table is seeded with the full ISO list. A code that is missing from the list is
    inserted with an empty ``name`` instead of rejecting the notice that uses it.
    """

    __tablename__ = "countries"

    code: Mapped[str] = mapped_column(CHAR(2), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
