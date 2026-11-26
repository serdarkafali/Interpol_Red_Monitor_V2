"""The choices of the left filter bar, read from the URL and validated (ADR 0013).

The filter bar is a plain GET form, so every choice ends up in the URL
(``/?sex=M&nationality=TR&age_min=30``). Anyone can type a URL, so the values are checked
here before any of them reaches a query.
"""

from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# Notices shown on one page.
PAGE_SIZE = 24
MAX_AGE = 120
MAX_QUERY_LENGTH = 100
# A page number far past the last page is no use and makes OFFSET scan many rows.
MAX_PAGE = 1000

# Two capital letters, as country codes are stored (ISO 3166-1 alpha-2).
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]


class NoticeFilters(BaseModel):
    """Everything the user chose in the filter bar, plus sorting and the page number.

    Every field has a default, so ``/`` alone means: active notices, newest first,
    first page. A list field can be given several times in the URL
    (``?nationality=TR&nationality=DE``); FastAPI collects the values into the list.
    """

    # An unknown parameter is an error, not silently ignored: a typo such as
    # ?natinality=TR would otherwise look like a working filter.
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Searched in the name and the forename.
    q: str | None = Field(default=None, max_length=MAX_QUERY_LENGTH)
    sex: list[Literal["M", "F", "U"]] = []
    nationality: list[CountryCode] = []
    # Country that issued an arrest warrant.
    issuer: list[CountryCode] = []
    age_min: int | None = Field(default=None, ge=0, le=MAX_AGE)
    age_max: int | None = Field(default=None, ge=0, le=MAX_AGE)
    has_photo: bool = False
    has_match: bool = False
    status: Literal["active", "removed"] = "active"
    sort: Literal["newest", "name"] = "newest"
    page: int = Field(default=1, ge=1, le=MAX_PAGE)

    @field_validator("q", "age_min", "age_max", mode="before")
    @classmethod
    def empty_means_not_given(cls, value: Any) -> Any:
        """Treat an empty box as "not chosen".

        A form sends its empty text and number boxes as ``q=`` and ``age_min=``; without
        this an empty age box would fail as "not a number".
        """
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def ages_in_order(self) -> Self:
        """Swap the ages if the minimum is above the maximum; the intent is clear."""
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            return self.model_copy(update={"age_min": self.age_max, "age_max": self.age_min})
        return self

    @property
    def offset(self) -> int:
        """How many notices the pages before this one hold (SQL OFFSET)."""
        return (self.page - 1) * PAGE_SIZE
