"""Tests of the list page queries against a real, migrated PostgreSQL container.

The fixtures live here and not in a conftest.py: mypy checks every test folder, and
another top-level ``conftest`` module would clash with the other packages' ones. All
notices are made up.
"""

import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, insert, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from testcontainers.community.postgres import PostgresContainer

from interpol_db import (
    EMBEDDING_DIMENSIONS,
    ArrestWarrant,
    Country,
    Face,
    FaceMatch,
    Notice,
    NoticePhoto,
    notice_nationalities,
)
from interpol_web.filters import PAGE_SIZE, NoticeFilters
from interpol_web.queries import (
    FacetOption,
    NoticeQueries,
    SearchResult,
    _age,
    _search_pattern,
    _years_before,
)

# Same image as docker-compose.yml.
POSTGRES_IMAGE = "pgvector/pgvector:0.8.2-pg18"
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "libs" / "db" / "alembic.ini"
TODAY = date(2026, 11, 26)
CREATED = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Engine of a throwaway database migrated to the latest revision."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        with pytest.MonkeyPatch.context() as monkeypatch:
            # env.py prefers DATABASE_URL, so the development database is never touched.
            monkeypatch.setenv("DATABASE_URL", url)
            command.upgrade(Config(ALEMBIC_INI), "head")
        engine = create_engine(url)
        yield engine
        engine.dispose()


@pytest.fixture
def queries(engine: Engine) -> Iterator[NoticeQueries]:
    """The queries on an empty database; every test adds its own notices."""
    yield NoticeQueries(engine.connect, today=lambda: TODAY)
    # NoticeQueries opens its own connections, so the rows are removed afterwards
    # instead of being rolled back.
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE notices, countries CASCADE"))


def add_notice(
    engine: Engine,
    entity_id: str,
    *,
    name: str = "TEST",
    forename: str | None = None,
    sex: str | None = None,
    born: date | None = None,
    created_minutes_later: int = 0,
    removed: bool = False,
    nationalities: tuple[str, ...] = (),
    issuers: tuple[str, ...] = (),
    photo: bool = False,
) -> int:
    """Insert a made-up notice with its countries and an optional photo; return its id."""
    with engine.begin() as connection:
        notice_id: int = connection.execute(
            insert(Notice)
            .values(
                entity_id=entity_id,
                name=name,
                forename=forename,
                sex_id=sex,
                date_of_birth=born,
                content_hash="hash",
                created_at=CREATED + timedelta(minutes=created_minutes_later),
                deleted_at=CREATED if removed else None,
            )
            .returning(Notice.id)
        ).scalar_one()
        for code in {*nationalities, *issuers}:
            connection.execute(
                pg_insert(Country)
                .values(code=code, name=f"Country {code}")
                .on_conflict_do_nothing()
            )
        for code in nationalities:
            connection.execute(
                insert(notice_nationalities).values(notice_id=notice_id, country_code=code)
            )
        for code in issuers:
            connection.execute(
                insert(ArrestWarrant).values(
                    notice_id=notice_id, issuing_country_code=code, charge="made up"
                )
            )
        if photo:
            connection.execute(
                insert(NoticePhoto).values(
                    notice_id=notice_id,
                    picture_id=f"pic-{entity_id}",
                    object_key=f"notices/{entity_id}/pic",
                    sha256="abc",
                    downloaded_at=CREATED,
                )
            )
    return notice_id


def add_face(engine: Engine, notice_id: int, picture_id: str, *, x: int = 0) -> int:
    """Insert a downloaded photo with one made-up face; return the face id."""
    vector = [1 / math.sqrt(EMBEDDING_DIMENSIONS)] * EMBEDDING_DIMENSIONS
    with engine.begin() as connection:
        photo_id = connection.execute(
            insert(NoticePhoto)
            .values(notice_id=notice_id, picture_id=picture_id, downloaded_at=CREATED)
            .returning(NoticePhoto.id)
        ).scalar_one()
        face_id: int = connection.execute(
            insert(Face)
            .values(
                photo_id=photo_id,
                box_x=x,
                box_y=0,
                box_width=50,
                box_height=50,
                detection_score=0.9,
                embedding=vector,
                model="test",
            )
            .returning(Face.id)
        ).scalar_one()
    return face_id


def add_match(
    engine: Engine, face_a: int, face_b: int, *, same_notice: bool, distance: float = 0.3
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(FaceMatch).values(
                first_face_id=min(face_a, face_b),
                second_face_id=max(face_a, face_b),
                distance=distance,
                same_notice=same_notice,
            )
        )


def search(queries: NoticeQueries, **filters: Any) -> SearchResult:
    return queries.search(NoticeFilters(**filters))


def entity_ids(result: SearchResult) -> list[str]:
    return [card.entity_id for card in result.cards]


def counts(options: tuple[FacetOption, ...]) -> dict[str, int]:
    return {option.value: option.count for option in options}


def test_an_empty_database_gives_an_empty_page(queries: NoticeQueries) -> None:
    result = search(queries)

    assert result.cards == ()
    assert result.total == 0
    assert counts(result.sexes) == {"M": 0, "F": 0, "U": 0}
    assert result.nationalities == ()


def test_active_notices_are_listed_newest_first(engine: Engine, queries: NoticeQueries) -> None:
    add_notice(engine, "2026/1", created_minutes_later=1)
    add_notice(engine, "2026/2", created_minutes_later=2)
    add_notice(engine, "2026/3", created_minutes_later=3, removed=True)

    result = search(queries)

    assert entity_ids(result) == ["2026/2", "2026/1"]
    assert result.total == 2


def test_removed_notices_are_listed_on_their_own(engine: Engine, queries: NoticeQueries) -> None:
    add_notice(engine, "2026/1")
    add_notice(engine, "2026/2", removed=True)

    assert entity_ids(search(queries, status="removed")) == ["2026/2"]


def test_sort_by_name(engine: Engine, queries: NoticeQueries) -> None:
    add_notice(engine, "2026/1", name="ZETA", created_minutes_later=2)
    add_notice(engine, "2026/2", name="ALFA", created_minutes_later=1)

    assert entity_ids(search(queries, sort="name")) == ["2026/2", "2026/1"]


def test_the_search_finds_full_names_and_entity_ids(engine: Engine, queries: NoticeQueries) -> None:
    add_notice(engine, "2026/111", name="DOE", forename="JOHN")
    add_notice(engine, "2026/222", name="ROE", forename="JANE")

    assert entity_ids(search(queries, q="john d")) == ["2026/111"]
    assert entity_ids(search(queries, q="2026/22")) == ["2026/222"]


def test_percent_and_underscore_in_the_search_are_plain_characters(
    engine: Engine, queries: NoticeQueries
) -> None:
    add_notice(engine, "2026/1", name="DOE")

    assert search(queries, q="%").cards == ()
    assert search(queries, q="D_E").cards == ()
    assert _search_pattern("50%_a\\b") == "%50\\%\\_a\\\\b%"


def test_options_of_one_group_are_joined_with_or(engine: Engine, queries: NoticeQueries) -> None:
    add_notice(engine, "2026/1", nationalities=("RU",))
    add_notice(engine, "2026/2", nationalities=("DE",))
    add_notice(engine, "2026/3", nationalities=("FR",))

    result = search(queries, nationality=["RU", "DE"])

    assert sorted(entity_ids(result)) == ["2026/1", "2026/2"]


def test_groups_are_joined_with_and(engine: Engine, queries: NoticeQueries) -> None:
    add_notice(engine, "2026/1", sex="F", nationalities=("RU",))
    add_notice(engine, "2026/2", sex="M", nationalities=("RU",))
    add_notice(engine, "2026/3", sex="F", nationalities=("DE",))

    assert entity_ids(search(queries, sex=["F"], nationality=["RU"])) == ["2026/1"]


def test_a_facet_ignores_its_own_filter_but_not_the_others(
    engine: Engine, queries: NoticeQueries
) -> None:
    add_notice(engine, "2026/1", sex="F", nationalities=("RU",))
    add_notice(engine, "2026/2", sex="M", nationalities=("RU",))
    add_notice(engine, "2026/3", sex="M", nationalities=("DE",))

    result = search(queries, sex=["F"])

    # The other sex keeps its count, so it can still be ticked.
    assert counts(result.sexes) == {"M": 2, "F": 1, "U": 0}
    # The nationality counts follow the sex filter.
    assert counts(result.nationalities) == {"RU": 1}


def test_country_facets_are_sorted_by_count_and_carry_the_name(
    engine: Engine, queries: NoticeQueries
) -> None:
    add_notice(engine, "2026/1", nationalities=("DE",))
    add_notice(engine, "2026/2", nationalities=("RU", "DE"))

    result = search(queries)

    assert result.nationalities == (
        FacetOption("DE", "Country DE", 2),
        FacetOption("RU", "Country RU", 1),
    )


def test_a_ticked_country_without_notices_stays_in_the_list(
    engine: Engine, queries: NoticeQueries
) -> None:
    add_notice(engine, "2026/1", sex="M", nationalities=("RU",))

    result = search(queries, sex=["F"], nationality=["RU"])

    assert result.nationalities == (FacetOption("RU", "RU", 0),)


def test_two_warrants_from_one_country_count_the_notice_once(
    engine: Engine, queries: NoticeQueries
) -> None:
    add_notice(engine, "2026/1", issuers=("US",))
    with engine.begin() as connection:
        notice_id = connection.execute(text("SELECT id FROM notices")).scalar_one()
        connection.execute(
            insert(ArrestWarrant).values(
                notice_id=notice_id, issuing_country_code="US", charge="made up 2"
            )
        )

    result = search(queries, issuer=["US"])

    assert result.total == 1
    assert counts(result.issuers) == {"US": 1}


def test_age_range_includes_both_ends(engine: Engine, queries: NoticeQueries) -> None:
    add_notice(engine, "2026/29", born=date(1997, 11, 27))  # turns 29 tomorrow: 28
    add_notice(engine, "2026/30", born=date(1996, 11, 26))  # 30 today
    add_notice(engine, "2026/50", born=date(1975, 11, 27))  # 50, turns 51 tomorrow
    add_notice(engine, "2026/51", born=date(1975, 11, 26))  # 51 today
    add_notice(engine, "2026/none")

    result = search(queries, age_min=30, age_max=50)

    assert sorted(entity_ids(result)) == ["2026/30", "2026/50"]


def test_only_notices_with_a_downloaded_photo(engine: Engine, queries: NoticeQueries) -> None:
    add_notice(engine, "2026/1", photo=True)
    add_notice(engine, "2026/2")

    assert entity_ids(search(queries, has_photo=True)) == ["2026/1"]


def test_only_matches_with_another_notice_count(engine: Engine, queries: NoticeQueries) -> None:
    first = add_notice(engine, "2026/1")
    second = add_notice(engine, "2026/2")
    alone = add_notice(engine, "2026/3")
    add_notice(engine, "2026/4")
    add_match(
        engine, add_face(engine, first, "a"), add_face(engine, second, "b"), same_notice=False
    )
    add_match(engine, add_face(engine, alone, "c"), add_face(engine, alone, "d"), same_notice=True)

    assert sorted(entity_ids(search(queries, has_match=True))) == ["2026/1", "2026/2"]


def test_the_second_page_continues_where_the_first_stopped(
    engine: Engine, queries: NoticeQueries
) -> None:
    for number in range(PAGE_SIZE + 3):
        add_notice(engine, f"2026/{number}", created_minutes_later=number)

    first = search(queries)
    second = search(queries, page=2)

    assert len(first.cards) == PAGE_SIZE
    assert entity_ids(second) == ["2026/2", "2026/1", "2026/0"]
    assert first.total == second.total == PAGE_SIZE + 3


def test_a_card_carries_age_nationalities_and_photo(engine: Engine, queries: NoticeQueries) -> None:
    notice_id = add_notice(
        engine,
        "2026/1",
        name="DOE",
        forename="JOHN",
        sex="M",
        born=date(1990, 1, 1),
        nationalities=("RU", "DE"),
        photo=True,
    )

    (card,) = search(queries).cards

    assert card.id == notice_id
    assert (card.name, card.forename, card.sex, card.age) == ("DOE", "JOHN", "M", 36)
    assert card.nationalities == ("DE", "RU")
    assert card.photo_id is not None


def test_age_counts_full_years() -> None:
    assert _age(date(2000, 11, 26), TODAY) == 26
    assert _age(date(2000, 11, 27), TODAY) == 25
    assert _age(None, TODAY) is None


def test_29_february_becomes_28_february_in_other_years() -> None:
    assert _years_before(date(2028, 2, 29), 1) == date(2027, 2, 28)
    assert _years_before(date(2028, 2, 29), 4) == date(2024, 2, 29)


def test_a_search_reads_one_snapshot(engine: Engine) -> None:
    seen: list[str] = []

    @contextmanager
    def connect() -> Iterator[Connection]:
        with engine.connect() as connection:
            yield connection
            seen.append(connection.get_isolation_level())

    NoticeQueries(connect, today=lambda: TODAY).search(NoticeFilters())

    assert seen == ["REPEATABLE READ"]


def test_an_unknown_notice_gives_none(queries: NoticeQueries) -> None:
    assert queries.notice(123456) is None


def test_the_detail_has_the_notice_and_its_countries(
    engine: Engine, queries: NoticeQueries
) -> None:
    notice_id = add_notice(
        engine,
        "2026/1",
        name="DOE",
        forename="JOHN",
        sex="M",
        born=date(1990, 11, 27),
        nationalities=("RU", "DE"),
        issuers=("US",),
    )

    detail = queries.notice(notice_id)

    assert detail is not None
    assert (detail.entity_id, detail.name, detail.forename, detail.sex) == (
        "2026/1",
        "DOE",
        "JOHN",
        "M",
    )
    assert detail.age == 35
    assert detail.removed_at is None
    assert [c.code for c in detail.nationalities] == ["DE", "RU"]
    assert detail.nationalities[0].name == "Country DE"
    assert [(w.country.code, w.charge) for w in detail.warrants] == [("US", "made up")]


def test_photos_come_with_their_faces_and_photos_without_faces_too(
    engine: Engine, queries: NoticeQueries
) -> None:
    notice_id = add_notice(engine, "2026/1", photo=True)
    face_a = add_face(engine, notice_id, "with-face-a", x=5)

    detail = queries.notice(notice_id)

    assert detail is not None
    assert [len(photo.faces) for photo in detail.photos] == [0, 1]
    (face,) = detail.photos[1].faces
    assert (face.face_id, face.x, face.width, face.height) == (face_a, 5, 50, 50)


def test_possible_matches_are_other_notices_closest_first(
    engine: Engine, queries: NoticeQueries
) -> None:
    own = add_notice(engine, "2026/1")
    near = add_notice(engine, "2026/2", name="NEAR")
    far = add_notice(engine, "2026/3", name="FAR", removed=True)
    own_face = add_face(engine, own, "own")
    second_own_face = add_face(engine, own, "own-2")
    near_face = add_face(engine, near, "near")
    far_face = add_face(engine, far, "far")
    # The own face is once the first and once the second of a pair.
    add_match(engine, own_face, far_face, same_notice=False, distance=0.5)
    add_match(engine, near_face, own_face, same_notice=False, distance=0.2)
    add_match(engine, own_face, second_own_face, same_notice=True, distance=0.1)

    detail = queries.notice(own)

    assert detail is not None
    assert [(m.other_notice_id, m.other_name, m.other_removed) for m in detail.matches] == [
        (near, "NEAR", False),
        (far, "FAR", True),
    ]
    assert [m.face_id for m in detail.matches] == [own_face, own_face]
    assert [m.other_face_id for m in detail.matches] == [near_face, far_face]
    assert detail.matches[0].similarity == pytest.approx(0.8)


def test_only_a_downloaded_photo_has_a_key(engine: Engine, queries: NoticeQueries) -> None:
    notice_id = add_notice(engine, "2026/1", photo=True)
    with engine.begin() as connection:
        downloaded = connection.execute(text("SELECT id FROM notice_photos")).scalar_one()
        waiting = connection.execute(
            insert(NoticePhoto)
            .values(notice_id=notice_id, picture_id="not-yet")
            .returning(NoticePhoto.id)
        ).scalar_one()

    assert queries.photo_key(downloaded) == "notices/2026/1/pic"
    assert queries.photo_key(waiting) is None
    assert queries.photo_key(999999) is None
