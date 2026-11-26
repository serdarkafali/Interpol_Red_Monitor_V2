"""Read-only queries of the notice list, detail and possible match pages (ADR 0013).

One search runs three kinds of query on the same snapshot of the database: the notices
of the current page, the total number of matching notices and the counts shown next to
every option of the sidebar (facets). A facet ignores its own filter, so the other
options of the same group keep their counts and stay clickable, like on sahibinden.com.
The detail page reads one notice with its countries, warrants, photo faces and possible
face matches. The match page lists the pairs of faces of different notices. The web
service connects with the read-only database user; nothing here
writes.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Connection,
    Label,
    RowMapping,
    Select,
    case,
    func,
    or_,
    select,
)
from sqlalchemy.orm import InstrumentedAttribute, aliased

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
# The similarity threshold of ADR 0012. Two faces of one notice below it may not show
# the same person; the face worker saves faces of other notices only above it.
MATCH_SIMILARITY_THRESHOLD = 0.443


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


@dataclass(frozen=True)
class CountryName:
    """A country code with its name; the name is missing for codes Interpol never named."""

    code: str
    name: str | None


@dataclass(frozen=True)
class Warrant:
    """One arrest warrant of a notice."""

    country: CountryName
    charge: str
    charge_translation: str | None


@dataclass(frozen=True)
class FaceBox:
    """A face found on a photo, in pixels of the original image."""

    face_id: int
    x: int
    y: int
    width: int
    height: int
    detection_score: float


@dataclass(frozen=True)
class PhotoFaces:
    """A downloaded photo of the notice and the faces found on it."""

    photo_id: int
    faces: tuple[FaceBox, ...]


@dataclass(frozen=True)
class PossibleMatch:
    """A face of this notice that looks like a face of another notice.

    Only a lead for a human to check, never an identification (ADR 0012).
    """

    face_id: int
    other_face_id: int
    other_photo_id: int
    other_notice_id: int
    other_entity_id: str
    other_name: str
    other_forename: str | None
    other_removed: bool
    # 1 - cosine distance; 0.443 is the threshold of ADR 0012, 1 the same direction.
    similarity: float


@dataclass(frozen=True)
class OwnComparison:
    """Two faces on photos of the same notice and how alike they are."""

    first_photo_id: int
    second_photo_id: int
    similarity: float

    @property
    def may_differ(self) -> bool:
        """True if the two photos may not show the same person (ADR 0013)."""
        return self.similarity < MATCH_SIMILARITY_THRESHOLD


@dataclass(frozen=True)
class MatchSide:
    """One face of a possible match: the notice, the photo and the face box."""

    notice_id: int
    entity_id: str
    name: str
    forename: str | None
    removed: bool
    photo_id: int
    box: FaceBox


@dataclass(frozen=True)
class MatchPair:
    """Two faces of different notices that look alike; a lead, never an identification."""

    first: MatchSide
    second: MatchSide
    similarity: float


@dataclass(frozen=True)
class MatchPage:
    """One page of the possible match list."""

    pairs: tuple[MatchPair, ...]
    total: int


@dataclass(frozen=True)
class NoticeDetail:
    """Everything the detail page shows about one notice."""

    id: int
    entity_id: str
    name: str
    forename: str | None
    sex: str | None
    date_of_birth: date | None
    age: int | None
    place_of_birth: str | None
    country_of_birth: CountryName | None
    height: Decimal | None
    weight: int | None
    distinguishing_marks: str | None
    languages: tuple[str, ...]
    eye_colors: tuple[str, ...]
    hair_colors: tuple[str, ...]
    first_seen_at: datetime
    last_seen_at: datetime
    removed_at: datetime | None
    nationalities: tuple[CountryName, ...]
    warrants: tuple[Warrant, ...]
    photos: tuple[PhotoFaces, ...]
    matches: tuple[PossibleMatch, ...]
    own_comparisons: tuple[OwnComparison, ...]


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

    def photo_key(self, photo_id: int) -> str | None:
        """The store key of a downloaded photo, or None if there is no such photo."""
        with self._connect() as connection:
            key: str | None = connection.execute(
                select(NoticePhoto.object_key).where(
                    NoticePhoto.id == photo_id, NoticePhoto.downloaded_at.is_not(None)
                )
            ).scalar_one_or_none()
            return key

    def matches(self, page: int) -> MatchPage:
        """One page of the possible matches between different notices, most alike first."""
        with self._connect() as connection:
            connection.execution_options(isolation_level="REPEATABLE READ")
            return MatchPage(pairs=_match_pairs(connection, page), total=_match_total(connection))

    def notice(self, notice_id: int) -> NoticeDetail | None:
        """Return one notice with everything around it, or None if there is no such id."""
        today = self._today()
        with self._connect() as connection:
            # The same snapshot for the notice and its warrants, photos and matches.
            connection.execution_options(isolation_level="REPEATABLE READ")
            return _notice_detail(connection, notice_id, today)


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


def _notice_detail(connection: Connection, notice_id: int, today: date) -> NoticeDetail | None:
    """The notice row and the four lists around it."""
    # A Connection returns plain rows, not ORM objects, so the columns are named one by one.
    row = connection.execute(
        select(
            Notice.entity_id,
            Notice.name,
            Notice.forename,
            Notice.sex_id,
            Notice.date_of_birth,
            Notice.place_of_birth,
            Notice.country_of_birth_code,
            Country.name.label("country_of_birth_name"),
            Notice.height,
            Notice.weight,
            Notice.distinguishing_marks,
            Notice.language_codes,
            Notice.eye_color_codes,
            Notice.hair_color_codes,
            Notice.created_at,
            Notice.last_seen_at,
            Notice.deleted_at,
        )
        .outerjoin(Country, Country.code == Notice.country_of_birth_code)
        .where(Notice.id == notice_id)
    ).one_or_none()
    if row is None:
        return None
    return NoticeDetail(
        id=notice_id,
        entity_id=row.entity_id,
        name=row.name,
        forename=row.forename,
        sex=row.sex_id,
        date_of_birth=row.date_of_birth,
        age=_age(row.date_of_birth, today),
        place_of_birth=row.place_of_birth,
        country_of_birth=(
            CountryName(row.country_of_birth_code, row.country_of_birth_name)
            if row.country_of_birth_code
            else None
        ),
        height=row.height,
        weight=row.weight,
        distinguishing_marks=row.distinguishing_marks,
        languages=tuple(row.language_codes),
        eye_colors=tuple(row.eye_color_codes),
        hair_colors=tuple(row.hair_color_codes),
        first_seen_at=row.created_at,
        last_seen_at=row.last_seen_at,
        removed_at=row.deleted_at,
        nationalities=_notice_nationalities(connection, notice_id),
        warrants=_warrants(connection, notice_id),
        photos=_photo_faces(connection, notice_id),
        matches=_possible_matches(connection, notice_id),
        own_comparisons=_own_comparisons(connection, notice_id),
    )


def _notice_nationalities(connection: Connection, notice_id: int) -> tuple[CountryName, ...]:
    rows = connection.execute(
        select(nationalities.c.country_code, Country.name)
        .join(Country, Country.code == nationalities.c.country_code)
        .where(nationalities.c.notice_id == notice_id)
        .order_by(nationalities.c.country_code)
    ).all()
    return tuple(CountryName(code, name) for code, name in rows)


def _warrants(connection: Connection, notice_id: int) -> tuple[Warrant, ...]:
    rows = connection.execute(
        select(
            ArrestWarrant.issuing_country_code,
            Country.name,
            ArrestWarrant.charge,
            ArrestWarrant.charge_translation,
        )
        .join(Country, Country.code == ArrestWarrant.issuing_country_code)
        .where(ArrestWarrant.notice_id == notice_id)
        .order_by(ArrestWarrant.id)
    ).all()
    return tuple(
        Warrant(CountryName(code, name), charge, translation)
        for code, name, charge, translation in rows
    )


def _photo_faces(connection: Connection, notice_id: int) -> tuple[PhotoFaces, ...]:
    """The downloaded photos in id order, each with its faces; photos without faces too."""
    rows = connection.execute(
        select(
            NoticePhoto.id,
            Face.id,
            Face.box_x,
            Face.box_y,
            Face.box_width,
            Face.box_height,
            Face.detection_score,
        )
        .outerjoin(Face, Face.photo_id == NoticePhoto.id)
        .where(NoticePhoto.notice_id == notice_id, NoticePhoto.downloaded_at.is_not(None))
        .order_by(NoticePhoto.id, Face.id)
    ).all()
    faces: dict[int, list[FaceBox]] = {}
    for photo_id, face_id, x, y, width, height, score in rows:
        boxes = faces.setdefault(photo_id, [])
        if face_id is not None:
            boxes.append(FaceBox(face_id, x, y, width, height, score))
    return tuple(PhotoFaces(photo_id, tuple(boxes)) for photo_id, boxes in faces.items())


def _possible_matches(connection: Connection, notice_id: int) -> tuple[PossibleMatch, ...]:
    """Faces of other notices saved as close to a face of this notice, closest first."""
    own_face = aliased(Face)
    own_photo = aliased(NoticePhoto)
    other_face = aliased(Face)
    other_photo = aliased(NoticePhoto)
    # A pair is saved once with the smaller face id first, so this notice's face can be
    # on either side; the other side is the other notice's face.
    other_face_id = case(
        (FaceMatch.first_face_id == own_face.id, FaceMatch.second_face_id),
        else_=FaceMatch.first_face_id,
    )
    rows = connection.execute(
        select(
            own_face.id,
            other_face.id,
            other_photo.id,
            Notice.id,
            Notice.entity_id,
            Notice.name,
            Notice.forename,
            Notice.deleted_at,
            FaceMatch.distance,
        )
        .select_from(own_photo)
        .join(own_face, own_face.photo_id == own_photo.id)
        .join(
            FaceMatch,
            or_(FaceMatch.first_face_id == own_face.id, FaceMatch.second_face_id == own_face.id),
        )
        .join(other_face, other_face.id == other_face_id)
        .join(other_photo, other_photo.id == other_face.photo_id)
        .join(Notice, Notice.id == other_photo.notice_id)
        .where(own_photo.notice_id == notice_id, FaceMatch.same_notice.is_(False))
        .order_by(FaceMatch.distance, other_face.id)
    ).all()
    return tuple(
        PossibleMatch(
            face_id=row[0],
            other_face_id=row[1],
            other_photo_id=row[2],
            other_notice_id=row[3],
            other_entity_id=row.entity_id,
            other_name=row.name,
            other_forename=row.forename,
            other_removed=row.deleted_at is not None,
            similarity=1 - row.distance,
        )
        for row in rows
    )


def _own_comparisons(connection: Connection, notice_id: int) -> tuple[OwnComparison, ...]:
    """The face worker compares every face of a notice with its other faces; least alike
    first, so a photo that may show someone else is at the top."""
    first_face = aliased(Face)
    second_face = aliased(Face)
    rows = connection.execute(
        select(first_face.photo_id, second_face.photo_id, FaceMatch.distance)
        .select_from(FaceMatch)
        .join(first_face, first_face.id == FaceMatch.first_face_id)
        .join(second_face, second_face.id == FaceMatch.second_face_id)
        .join(NoticePhoto, NoticePhoto.id == first_face.photo_id)
        .where(NoticePhoto.notice_id == notice_id, FaceMatch.same_notice.is_(True))
        .order_by(FaceMatch.distance.desc(), FaceMatch.id)
    ).all()
    return tuple(
        OwnComparison(first_photo_id=first, second_photo_id=second, similarity=1 - distance)
        for first, second, distance in rows
    )


def _match_total(connection: Connection) -> int:
    query = select(func.count()).select_from(FaceMatch).where(FaceMatch.same_notice.is_(False))
    return connection.execute(query).scalar_one()


def _match_pairs(connection: Connection, page: int) -> tuple[MatchPair, ...]:
    """The pairs of one page; each side brings its notice, photo and face box."""
    # The same three tables are joined twice, once per side of the pair, so each side
    # gets its own aliases.
    first_face, second_face = aliased(Face), aliased(Face)
    first_photo, second_photo = aliased(NoticePhoto), aliased(NoticePhoto)
    first_notice, second_notice = aliased(Notice), aliased(Notice)
    rows = connection.execute(
        select(
            FaceMatch.distance,
            *_side_columns("first", first_notice, first_photo, first_face),
            *_side_columns("second", second_notice, second_photo, second_face),
        )
        .select_from(FaceMatch)
        .join(first_face, first_face.id == FaceMatch.first_face_id)
        .join(first_photo, first_photo.id == first_face.photo_id)
        .join(first_notice, first_notice.id == first_photo.notice_id)
        .join(second_face, second_face.id == FaceMatch.second_face_id)
        .join(second_photo, second_photo.id == second_face.photo_id)
        .join(second_notice, second_notice.id == second_photo.notice_id)
        .where(FaceMatch.same_notice.is_(False))
        # The id breaks ties, so a pair never shows up on two pages.
        .order_by(FaceMatch.distance, FaceMatch.id)
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
    ).all()
    return tuple(
        MatchPair(
            first=_match_side(row._mapping, "first"),
            second=_match_side(row._mapping, "second"),
            similarity=1 - row.distance,
        )
        for row in rows
    )


def _side_columns(
    side: str, notice: type[Notice], photo: type[NoticePhoto], face: type[Face]
) -> list[Label[Any]]:
    """The columns of one side, named "first_..." or "second_..."."""
    columns: dict[str, InstrumentedAttribute[Any]] = {
        "notice_id": notice.id,
        "entity_id": notice.entity_id,
        "name": notice.name,
        "forename": notice.forename,
        "deleted_at": notice.deleted_at,
        "photo_id": photo.id,
        "face_id": face.id,
        "box_x": face.box_x,
        "box_y": face.box_y,
        "box_width": face.box_width,
        "box_height": face.box_height,
        "detection_score": face.detection_score,
    }
    return [column.label(f"{side}_{name}") for name, column in columns.items()]


def _match_side(row: RowMapping, side: str) -> MatchSide:
    """Read the "first_..." or "second_..." columns of a row back into one side."""

    def value(name: str) -> Any:
        return row[f"{side}_{name}"]

    return MatchSide(
        notice_id=value("notice_id"),
        entity_id=value("entity_id"),
        name=value("name"),
        forename=value("forename"),
        removed=value("deleted_at") is not None,
        photo_id=value("photo_id"),
        box=FaceBox(
            value("face_id"),
            value("box_x"),
            value("box_y"),
            value("box_width"),
            value("box_height"),
            value("detection_score"),
        ),
    )
