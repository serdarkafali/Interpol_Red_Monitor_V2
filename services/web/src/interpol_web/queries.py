"""Read-only queries of the notice list page (ADR 0013).

One search runs three kinds of query on the same snapshot of the database: the notices
of the current page, the total number of matching notices and the counts shown next to
every option of the sidebar (facets). A facet ignores its own filter, so the other
options of the same group keep their counts and stay clickable, like on sahibinden.com.
The web service connects with the read-only database user; nothing here writes.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date

from sqlalchemy import (
    ColumnElement,
    Connection,
    Select,
    func,
    or_,
    select,
)

from interpol_db import ArrestWarrant, Country, Face, FaceMatch, Notice, NoticePhoto
from interpol_db import notice_nationalities as nationalities
from interpol_web.filters import PAGE_SIZE, NoticeFilters

# A function that opens a connection and yields it, e.g. ``engine.connect``.
Connect = Callable[[], AbstractContextManager[Connection]]

# The groups of the sidebar; a facet query leaves out the filter of its own group.
SEX = "sex"
NATIONALITY = "nationality"
ISSUER = "issuer"
# The sex codes of Interpol, in the order the sidebar lists them.
SEX_CODES = ("M", "F", "U")


@dataclass(frozen=True)
class NoticeCard:
    """What one card of the list shows. No photo bytes, only the id to ask for them."""

    id: int
    entity_id: str
    name: str
    forename: str | None
    sex: str | None
    age: int | None
    nationalities: tuple[str, ...]
    photo_id: int | None


@dataclass(frozen=True)
class FacetOption:
    """One option of a sidebar group, e.g. value "RU", label "Russia", count 40."""

    value: str
    label: str
    count: int


@dataclass(frozen=True)
class SearchResult:
    """Everything the list page needs for one set of filters."""

    cards: tuple[NoticeCard, ...]
    total: int
    sexes: tuple[FacetOption, ...]
    nationalities: tuple[FacetOption, ...]
    issuers: tuple[FacetOption, ...]


class NoticeQueries:
    """Runs the searches of the list page, each on its own connection."""

    def __init__(self, connect: Connect, today: Callable[[], date] = date.today) -> None:
        self._connect = connect
        # Ages depend on the day; tests pass a fixed one.
        self._today = today

    def search(self, filters: NoticeFilters) -> SearchResult:
        """Return the page, the total and the facet counts for the filters."""
        today = self._today()
        with self._connect() as connection:
            # REPEATABLE READ: every query below sees the same snapshot, so the total,
            # the cards and the counts agree even if the ingestor writes in between.
            # Nothing is written, so there is nothing to commit.
            connection.execution_options(isolation_level="REPEATABLE READ")
            return SearchResult(
                cards=_page(connection, filters, today),
                total=_total(connection, filters, today),
                sexes=_sex_facet(connection, filters, today),
                nationalities=_country_facet(connection, filters, today, NATIONALITY),
                issuers=_country_facet(connection, filters, today, ISSUER),
            )


def _conditions(
    filters: NoticeFilters, today: date, skip: str | None = None
) -> list[ColumnElement[bool]]:
    """The WHERE parts of the filters; ``skip`` leaves out one sidebar group.

    Options of one group are joined with OR (Russia or Germany), groups with AND
    (female and Russian).
    """
    conditions: list[ColumnElement[bool]] = []

    if filters.status == "active":
        conditions.append(Notice.deleted_at.is_(None))
    else:
        conditions.append(Notice.deleted_at.is_not(None))

    if filters.q:
        pattern = _search_pattern(filters.q)
        full_name = func.concat_ws(" ", Notice.forename, Notice.name)
        conditions.append(
            or_(
                full_name.ilike(pattern, escape="\\"),
                Notice.entity_id.ilike(pattern, escape="\\"),
            )
        )

    if filters.sex and skip != SEX:
        conditions.append(Notice.sex_id.in_(filters.sex))

    if filters.nationality and skip != NATIONALITY:
        conditions.append(
            Notice.id.in_(
                select(nationalities.c.notice_id).where(
                    nationalities.c.country_code.in_(filters.nationality)
                )
            )
        )

    if filters.issuer and skip != ISSUER:
        conditions.append(
            Notice.id.in_(
                select(ArrestWarrant.notice_id).where(
                    ArrestWarrant.issuing_country_code.in_(filters.issuer)
                )
            )
        )

    born_after, born_on_or_before = _birth_date_range(filters, today)
    if born_after is not None:
        conditions.append(Notice.date_of_birth > born_after)
    if born_on_or_before is not None:
        conditions.append(Notice.date_of_birth <= born_on_or_before)

    if filters.has_photo:
        conditions.append(
            Notice.id.in_(
                select(NoticePhoto.notice_id).where(NoticePhoto.downloaded_at.is_not(None))
            )
        )

    if filters.has_match:
        # Only matches with another notice: two photos of the same notice showing the
        # same person are expected and are not a lead.
        conditions.append(
            Notice.id.in_(
                select(NoticePhoto.notice_id)
                .join(Face, Face.photo_id == NoticePhoto.id)
                .join(
                    FaceMatch,
                    or_(FaceMatch.first_face_id == Face.id, FaceMatch.second_face_id == Face.id),
                )
                .where(FaceMatch.same_notice.is_(False))
            )
        )

    return conditions


def _birth_date_range(filters: NoticeFilters, today: date) -> tuple[date | None, date | None]:
    """Turn the age range into birth dates: (born after, born on or before).

    Someone is at least 30 if born on or before today 30 years ago, and at most 50 if
    born after today 51 years ago. Notices without a date of birth drop out as soon as
    an age is chosen.
    """
    born_after = None
    born_on_or_before = None
    if filters.age_max is not None:
        born_after = _years_before(today, filters.age_max + 1)
    if filters.age_min is not None:
        born_on_or_before = _years_before(today, filters.age_min)
    return born_after, born_on_or_before


def _years_before(day: date, years: int) -> date:
    """The same day ``years`` earlier; 29 February becomes 28 February in other years."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _age(date_of_birth: date | None, today: date) -> int | None:
    """Full years between the date of birth and today."""
    if date_of_birth is None:
        return None
    had_birthday = (today.month, today.day) >= (date_of_birth.month, date_of_birth.day)
    return today.year - date_of_birth.year - (0 if had_birthday else 1)


def _search_pattern(q: str) -> str:
    """A LIKE pattern finding ``q`` anywhere; % and _ in ``q`` are taken literally."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _page(connection: Connection, filters: NoticeFilters, today: date) -> tuple[NoticeCard, ...]:
    """The notices of the current page, newest first or by name."""
    # The first downloaded photo of the notice is shown on its card.
    photo_id = (
        select(func.min(NoticePhoto.id))
        .where(NoticePhoto.notice_id == Notice.id, NoticePhoto.downloaded_at.is_not(None))
        .scalar_subquery()
    )
    query = select(
        Notice.id,
        Notice.entity_id,
        Notice.name,
        Notice.forename,
        Notice.sex_id,
        Notice.date_of_birth,
        photo_id.label("photo_id"),
    ).where(*_conditions(filters, today))
    if filters.sort == "name":
        query = query.order_by(Notice.name, Notice.forename.nulls_first(), Notice.id)
    else:
        # The id breaks ties, so a notice never shows up on two pages.
        query = query.order_by(Notice.created_at.desc(), Notice.id.desc())
    rows = connection.execute(query.limit(PAGE_SIZE).offset(filters.offset)).all()

    countries = _nationalities_of(connection, [row.id for row in rows])
    return tuple(
        NoticeCard(
            id=row.id,
            entity_id=row.entity_id,
            name=row.name,
            forename=row.forename,
            sex=row.sex_id,
            age=_age(row.date_of_birth, today),
            nationalities=countries.get(row.id, ()),
            photo_id=row.photo_id,
        )
        for row in rows
    )


def _nationalities_of(
    connection: Connection, notice_ids: Sequence[int]
) -> dict[int, tuple[str, ...]]:
    """The nationality codes of the given notices, in one query instead of one each."""
    if not notice_ids:
        return {}
    rows = connection.execute(
        select(nationalities.c.notice_id, nationalities.c.country_code)
        .where(nationalities.c.notice_id.in_(notice_ids))
        .order_by(nationalities.c.notice_id, nationalities.c.country_code)
    ).all()
    codes: dict[int, list[str]] = {}
    for notice_id, country_code in rows:
        codes.setdefault(notice_id, []).append(country_code)
    return {notice_id: tuple(values) for notice_id, values in codes.items()}


def _total(connection: Connection, filters: NoticeFilters, today: date) -> int:
    """How many notices match all filters."""
    query = select(func.count()).select_from(Notice).where(*_conditions(filters, today))
    return connection.execute(query).scalar_one()


def _sex_facet(
    connection: Connection, filters: NoticeFilters, today: date
) -> tuple[FacetOption, ...]:
    """Notice counts per sex, with every filter except the sex filter."""
    rows = connection.execute(
        select(Notice.sex_id, func.count())
        .where(*_conditions(filters, today, skip=SEX), Notice.sex_id.in_(SEX_CODES))
        .group_by(Notice.sex_id)
    ).all()
    counts = dict(rows)
    # All three are always listed, in a fixed order, even with zero notices. The page
    # template turns the codes into words.
    return tuple(
        FacetOption(value=code, label=code, count=counts.get(code, 0)) for code in SEX_CODES
    )


def _country_facet(
    connection: Connection, filters: NoticeFilters, today: date, group: str
) -> tuple[FacetOption, ...]:
    """Notice counts per nationality or per country issuing a warrant, most first."""
    link: Select[int, str]
    if group == NATIONALITY:
        link = select(
            nationalities.c.notice_id.label("notice_id"),
            nationalities.c.country_code.label("code"),
        )
        selected = filters.nationality
    else:
        link = select(
            ArrestWarrant.notice_id.label("notice_id"),
            ArrestWarrant.issuing_country_code.label("code"),
        )
        selected = filters.issuer
    # (notice, country) pairs; a notice with two warrants from one country counts once.
    pairs = link.distinct().subquery()

    count = func.count()
    rows = connection.execute(
        select(pairs.c.code, Country.name, count)
        .select_from(Notice)
        .join(pairs, pairs.c.notice_id == Notice.id)
        .join(Country, Country.code == pairs.c.code)
        .where(*_conditions(filters, today, skip=group))
        .group_by(pairs.c.code, Country.name)
        .order_by(count.desc(), pairs.c.code)
    ).all()
    options = [
        FacetOption(value=code, label=name or code, count=number) for code, name, number in rows
    ]
    # A ticked country with no notices left must stay in the list, or it could not be
    # unticked.
    listed = {option.value for option in options}
    options += [
        FacetOption(value=code, label=code, count=0) for code in selected if code not in listed
    ]
    return tuple(options)
